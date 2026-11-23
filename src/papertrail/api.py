"""Shared Semantic Scholar HTTP client and single-page requests (Python 3.11+)."""

import argparse
import math
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
TIMEOUT = (5, 20)
MIN_INTERVAL = 2.0
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 10


class RequestBudget:
    """One collection's shared budget; retries consume attempts too."""
    def __init__(self, limit):
        self.limit = limit
        self.used = 0
        self.attempt_status_counts = {}

    def take(self):
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def budget_error():
    return {"status": "error", "stop_run": True, "response": None,
            "error": {"kind": "request_budget", "message": "Collection request budget exhausted; saved pages retained."}}


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
    source_key = "semantic-scholar"
    source = "Semantic Scholar"

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PaperTrail/0.1"})
        if api_key:
            self.session.headers["x-api-key"] = api_key
        self.last_started = None
        self.budget = None  # TASK-001 retains its original per-endpoint bounds.

    def metadata(self, paper_id):
        return self.request_json(
            "/paper/" + quote(paper_id, safe=":"), {"fields": ",".join(PAPER_FIELDS)},
        )

    def search_titles(self, query, *, limit=5):
        """One bounded relevance-search page; callers must confirm a candidate.

        Shares request_json timeouts, retry/pacing, redaction and request budget.
        This deliberately does not use the automatic best-title-match endpoint.
        """
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 500:
            raise ValueError("Search query must contain 1..500 characters.")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Search limit must be an integer in 1..10.")
        # Relevance search does not reliably match hyphenated query terms.
        # Normalize only this request; identifiers and the caller's text stay intact.
        search_query = " ".join(re.sub(r"[-\u2010-\u2015]", " ", query).split())
        if not search_query:
            raise ValueError("Search query must contain words, not only hyphens.")
        return self.request_json("/paper/search", {
            "query": search_query, "limit": limit, "offset": 0,
            "fields": ",".join(PAPER_FIELDS),
        })

    def relation_page(self, paper_id, direction, offset, limit):
        if direction not in {"references", "citations"}:
            raise ValueError("Unknown relation direction.")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Expected offset >= 0 and limit in 1..1000.")
        return self.request_json(
            "/paper/" + quote(paper_id, safe=":") + "/" + direction,
            {"fields": ",".join(RELATION_FIELDS), "offset": offset, "limit": limit},
        )

    def request_json(self, path, params):
        """Return safe evidence, with bounded retries and no exception/header dump."""
        result = {
            "endpoint": BASE_URL + path, "params": params, "status": "error",
            "attempts": [], "response": None, "stop_run": False,
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.budget is not None and not self.budget.take():
                result.update(budget_error(), fetched_at=utc_now())
                return redact(result, self.api_key)
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
                    if self.budget is not None:
                        status = str(response.status_code)
                        counts = self.budget.attempt_status_counts
                        counts[status] = counts.get(status, 0) + 1
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



def paper_id_valid(value):
    return isinstance(value, str) and bool(value.strip())


def parse_page(body, offset, limit):
    """Retain usable rows on pagination errors, but never guess the next offset."""
    rows = body.get("data")
    if not isinstance(rows, list):
        return [], None, "invalid_shape", "Expected a data list; null/missing is not empty."
    if len(rows) > limit:
        return rows, None, "response_exceeds_limit", "API returned more rows than requested; collection stopped."
    if "offset" in body and (type(body["offset"]) is not int or body["offset"] != offset):
        return rows, None, "invalid_pagination", "API response offset differs from requested offset."
    if "next" not in body:
        return rows, None, None, None
    next_offset = body["next"]
    # Offset paging must progress by exactly the number of returned records.
    if not rows or type(next_offset) is not int or next_offset != offset + len(rows):
        return rows, None, "invalid_pagination", "Non-progressing, empty or discontinuous next page."
    return rows, next_offset, None, None


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


