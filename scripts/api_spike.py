"""TASK-001: bounded, single-page Semantic Scholar probe (Python 3.11+)."""

import argparse
import json
import os
from pathlib import Path
import sys
import time  # Shared clock retained for the original tests.
from datetime import datetime, timezone
from urllib.parse import quote

# Preserve the direct-script CLI without requiring an editable installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from papertrail.api import (
    S2Client, PAPER_FIELDS, RELATION_FIELDS, input_id, page_limit,
    paper_id_valid, redact, retry_delay, utc_now,
)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"


def field_state(record, field):
    if field not in record:
        return "missing"
    value = record[field]
    if value is None:
        return "null"
    if isinstance(value, str) and not value.strip() or value == [] or value == {}:
        return "empty"
    return "available"  # Includes False and zero.


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
