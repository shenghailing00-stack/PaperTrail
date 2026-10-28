"""TASK-001: bounded, single-page Semantic Scholar probe (Python 3.11+)."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests


BASE_URL = "https://api.semanticscholar.org/graph/v1"
PAPER_FIELDS = (
    "paperId", "externalIds", "title", "year", "authors", "url", "abstract",
    "referenceCount", "citationCount",
)
# These endpoints accept bare paper fields, NOT citedPaper.title/citingPaper.title.
RELATION_FIELDS = ("contexts", "intents", "isInfluential", *PAPER_FIELDS)
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
TIMEOUT = (5, 20)
MIN_INTERVAL = 1.1
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 10


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def redact(value, secret):
    """Scrub the configured secret even if unexpectedly echoed in a response."""
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    if isinstance(value, dict):
        return {redact(key, secret): redact(item, secret) for key, item in value.items()}
    return value


def retry_delay(header, attempt):
    """Honor seconds or HTTP-date; never shorten a server-requested long wait."""
    fallback = 2 ** attempt
    if header is None:
        return fallback
    try:
        delay = float(header)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    if not math.isfinite(delay):
        return MAX_RETRY_WAIT + 1
    return max(fallback, delay)


def transient_ssl_error(error):
    """Retry only recognized interruptions, never certificate verification failures."""
    pending, seen, errors = [error], set(), []
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        errors.append(current)
        pending.extend(current.args)
        pending.extend((current.__cause__, current.__context__, getattr(current, "reason", None)))
    # requests/urllib3 wrap SSL errors; inspect locally without logging their text.
    detail = " ".join(str(item).lower() for item in errors)
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in errors) or any(
        marker in detail for marker in (
            "certificate_verify_failed", "certificate verify failed", "certificate verification failed",
            "hostname mismatch", "doesn't match", "does not match", "self-signed certificate",
        )
    ):
        return False
    return any(isinstance(item, ssl.SSLEOFError) for item in errors) or any(
        marker in detail for marker in (
            "unexpected_eof", "unexpected eof", "eof occurred", "connection reset", "connection aborted",
        )
    )


class S2Client:
    """One sequential session, sharing its pacing clock across endpoints/retries."""

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PaperTrail-TASK-001/0.1"})
        if api_key:
            self.session.headers["x-api-key"] = api_key
        self.last_started = None

    def request_json(self, path, params):
        """Return safe evidence, with bounded retries and no exception/header dump."""
        result = {
            "endpoint": BASE_URL + path, "params": params, "status": "error",
            "attempts": [], "response": None, "stop_run": False,
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.last_started is not None:
                time.sleep(max(0, MIN_INTERVAL - (time.monotonic() - self.last_started)))
            self.last_started = time.monotonic()
            event = {"started_at": utc_now()}
            result["attempts"].append(event)
            delay = 2 ** attempt
            retryable = False
            try:
                # Disable redirects: do not forward credentials or hide extra requests.
                with self.session.get(
                    BASE_URL + path, params=params, timeout=TIMEOUT, allow_redirects=False,
                ) as response:
                    event["http_status"] = response.status_code
                    if response.status_code == 200:
                        try:
                            body = response.json()
                        except ValueError:
                            kind, message = "invalid_json", "Response is not valid JSON."
                        else:
                            result["response"] = body
                            if isinstance(body, dict):
                                result.pop("error", None)
                                result.update(status="ok", fetched_at=utc_now())
                                return redact(result, self.api_key)
                            kind, message = "invalid_shape", "Expected a JSON object."
                    else:
                        status = response.status_code
                        kind, message = {
                            400: ("bad_request", "Invalid ID or request parameters."),
                            401: ("authentication", "API key rejected or authentication required."),
                            403: ("forbidden", "API access denied."),
                            404: ("not_found", "Paper not found; check the identifier."),
                            429: ("rate_limit", "Rate limited; stop after the bounded retry budget."),
                        }.get(status, ("http_error", f"HTTP {status}; request failed."))
                        retryable = status == 429 or 500 <= status < 600
                        if 500 <= status < 600:
                            kind = "server_error"
                        if retryable:
                            delay = retry_delay(response.headers.get("Retry-After"), attempt)
            except requests.exceptions.SSLError as error:
                kind, message = "ssl", "TLS connection/certificate error; verification remains enabled."
                retryable = transient_ssl_error(error)
                if retryable:
                    message = "Temporary TLS connection interruption; verification remains enabled."
            except requests.exceptions.Timeout:
                kind, message, retryable = "timeout", "Connection/read timeout.", True
            except requests.exceptions.ConnectionError:
                kind, message, retryable = "connection", "Network/proxy connection failed.", True
            except requests.exceptions.RequestException:
                kind, message = "request_error", "HTTP request could not be completed."
            event["error_kind"] = kind
            result.update(error={"kind": kind, "message": message}, fetched_at=utc_now())
            # Anonymous callers stop on the second 429, even with attempts remaining.
            anonymous_429s = sum(e.get("http_status") == 429 for e in result["attempts"])
            exhausted = attempt == MAX_ATTEMPTS or (
                not self.api_key and anonymous_429s >= 2
            )
            if retryable and not exhausted and delay <= MAX_RETRY_WAIT:
                event["retry_wait_seconds"] = delay
                time.sleep(delay)
                continue
            if retryable and delay > MAX_RETRY_WAIT:
                result["error"]["message"] += " Retry-After exceeds the 10-second wait budget."
            result["stop_run"] = kind in {
                "authentication", "forbidden", "rate_limit", "ssl", "connection",
                "timeout", "request_error", "server_error",
            }
            return redact(result, self.api_key)


def field_state(record, field):
    if field not in record:
        return "missing"
    value = record[field]
    if value is None:
        return "null"
    if isinstance(value, str) and not value.strip() or value == [] or value == {}:
        return "empty"
    return "available"  # Includes False and zero.


def paper_id_valid(value):
    return isinstance(value, str) and bool(value.strip())


def summarize_relations(payload, relation, seed_id, limit, expected_count=None):
    """Pure parsing: preserve unresolved rows; count availability and directed edges."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("Expected a data list; null/missing data is not an empty list.")
    nested = "citedPaper" if relation == "references" else "citingPaper"
    availability = {
        field: dict.fromkeys(("available", "empty", "null", "missing"), 0)
        for field in ("contexts", "intents", "isInfluential")
    }
    missing = dict.fromkeys(PAPER_FIELDS, 0)
    items, edges, seen_ids = [], set(), set()
    unresolved = malformed = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            row = {}
            malformed += 1
        paper = row.get(nested)
        if not isinstance(paper, dict):
            paper = {}
        other_id = paper.get("paperId")
        edge = None
        if not paper_id_valid(other_id):
            unresolved += 1
        else:
            seen_ids.add(other_id)
            edge = [seed_id, other_id] if relation == "references" else [other_id, seed_id]
            edges.add(tuple(edge))
        for field in PAPER_FIELDS:
            missing[field] += field_state(paper, field) != "available"
        states = {field: field_state(row, field) for field in availability}
        for field, state in states.items():
            availability[field][state] += 1
        items.append({
            "index": index, "paper_id": other_id, "edge": edge,
            "title": paper.get("title"), "year": paper.get("year"),
            "contexts": row.get("contexts"), "intents": row.get("intents"),
            "isInfluential": row.get("isInfluential"), "field_states": states,
        })
    next_value = payload.get("next")
    if "next" not in payload:
        pagination, truncated = "end_reported_by_api", False
    elif type(next_value) is int and next_value > 0:
        pagination, truncated = "more_available", True
    else:
        pagination, truncated = "unknown_next_value", None
    count_gap = (
        type(expected_count) is int and expected_count > len(rows)
        and pagination == "end_reported_by_api"
    )
    if count_gap:
        pagination, truncated = "count_mismatch_without_next", None
    if len(rows) > limit:
        pagination, truncated = "response_exceeds_requested_limit", None
    return {
        "returned": len(rows), "resolved": len(rows) - unresolved,
        "unresolved": unresolved, "malformed_items": malformed,
        "unique_papers": len(seen_ids), "unique_edges": len(edges),
        "duplicate_resolved_items": len(rows) - unresolved - len(seen_ids),
        "empty_list": not rows, "missing_paper_fields": missing,
        "availability": availability, "next_present": "next" in payload,
        "next": next_value, "pagination": pagination, "truncated": truncated,
        "metadata_count": expected_count, "items": items,
        "limitation": "One API page only; no next page does not prove bibliographic completeness.",
    }


def run_probe(client, paper_id, references_limit=5, citations_limit=5, references_only=False):
    """Orchestrate one paper, stopping dependent calls after a blocking failure."""
    report = {
        "source": "Semantic Scholar", "started_at": utc_now(), "input_id": paper_id,
        "authenticated": bool(client.api_key), "references_only": references_only,
        "request_budget": 3 if references_only else 9, "results": {},
    }
    canonical_id = paper_id
    metadata = {}
    endpoints = ["references"] if references_only else ["metadata", "references", "citations"]
    stopped = None
    for name in endpoints:
        if stopped:
            report["results"][name] = {"status": "skipped", "reason": stopped}
            continue
        path = "/paper/" + quote(canonical_id, safe=":")
        params = {"fields": ",".join(PAPER_FIELDS if name == "metadata" else RELATION_FIELDS)}
        if name != "metadata":
            path += "/" + name
            params.update(offset=0, limit=references_limit if name == "references" else citations_limit)
        result = client.request_json(path, params)
        report["results"][name] = result
        if result["status"] == "ok":
            body = result["response"]
            try:
                if name == "metadata":
                    if not paper_id_valid(body.get("paperId")):
                        raise ValueError("Metadata has no usable paperId.")
                    metadata = body
                    canonical_id = body["paperId"]
                    result["summary"] = {
                        "paper_id": canonical_id, "title": body.get("title"),
                        "field_states": {field: field_state(body, field) for field in PAPER_FIELDS},
                    }
                else:
                    result["summary"] = summarize_relations(
                        body, name, canonical_id, params["limit"],
                        metadata.get("referenceCount" if name == "references" else "citationCount"),
                    )
            except ValueError as error:
                result.update(status="error", error={"kind": "invalid_shape", "message": str(error)})
        if result["status"] == "error" and (name == "metadata" or result["stop_run"]):
            stopped = result["error"]["kind"]
    report["http_attempts"] = sum(len(r.get("attempts", [])) for r in report["results"].values())
    report["status"] = "ok" if all(r["status"] == "ok" for r in report["results"].values()) else "incomplete"
    return redact(report, client.api_key)


def page_limit(value):
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("limit must be an integer in 1..1000") from None
    if not 1 <= limit <= 1000:
        raise argparse.ArgumentTypeError("limit must be in 1..1000")
    return limit


def input_id(value):
    value = value.strip()
    # Validate shape only: existence and exact publisher syntax are API decisions.
    if re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return value
    prefix, separator, suffix = value.partition(":")
    if (separator and prefix in {"ARXIV", "DOI", "CorpusId", "ACL", "MAG", "PMID", "PMCID", "URL"}
            and suffix and not any(char.isspace() or ord(char) < 32 for char in value)):
        return value
    raise argparse.ArgumentTypeError("Use a Semantic Scholar ID or a prefixed ID, e.g. ARXIV:1706.03762.")


def print_summary(report):
    """Print endpoint quality summaries and at most three edges across the run."""
    print(f"Status: {report['status']}; HTTP attempts: {report['http_attempts']}")
    metadata = report["results"].get("metadata", {}).get("response") or {}
    seed = {
        "paper_id": metadata.get("paperId", report["input_id"]),
        "title": metadata.get("title"), "year": metadata.get("year"),
    }
    examples = []
    for name, result in report["results"].items():
        summary = result.get("summary", {})
        quality = {key: value for key, value in summary.items() if key != "items"}
        quality["status"] = result["status"]
        for key in ("error", "reason"):
            if key in result:
                quality[key] = result[key]
        print(f"{name}: " + json.dumps(quality, ensure_ascii=True))
        edges, seen = [], set()
        for item in summary.get("items", []):
            edge = item.get("edge")
            if not edge or tuple(edge) in seen:
                continue
            seen.add(tuple(edge))
            other = {key: item.get(key) for key in ("paper_id", "title", "year")}
            citing, cited = (seed, other) if name == "references" else (other, seed)
            edges.append(f"Example {name}: " + json.dumps(citing, ensure_ascii=True)
                         + " -> " + json.dumps(cited, ensure_ascii=True))
            if len(edges) == 3:
                break
        examples.append(edges)
    # Alternate endpoints so both directions appear when available.
    lines = [group[index] for index in range(3) for group in examples if index < len(group)]
    for line in lines[:3]:
        print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_id", type=input_id)
    parser.add_argument("--references-limit", type=page_limit, default=5)
    parser.add_argument("--citations-limit", type=page_limit, default=5)
    parser.add_argument("--references-only", action="store_true", help="Only one references page; no metadata/citations")
    args = parser.parse_args(argv)
    secret = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None
    client = S2Client(secret)
    try:
        report = run_probe(client, args.paper_id, args.references_limit, args.citations_limit, args.references_only)
    finally:
        client.session.close()
    # Timestamp names avoid unsafe ID characters on Windows and do not overwrite runs.
    path = OUTPUT_DIR / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except OSError:
        print("Local JSON save failed; check disk space and write permissions.")
        return 1
    print_summary(report)
    print("Saved: " + str(path.relative_to(OUTPUT_DIR.parent.parent)))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
