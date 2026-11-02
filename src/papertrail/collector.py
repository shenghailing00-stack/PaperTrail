"""Collect only seed neighbors, using deterministic shared citation quotas."""

from .api import input_id, paper_id_valid, parse_page, redact, utc_now
from .store import fresh, normalize_alias


DEFAULT_TTL = 7 * 24 * 60 * 60


def new_state(seed, direction, limit):
    now = utc_now()
    return dict(seed_id=seed, direction=direction, status="partial", next_offset=0,
                raw_count=0, unresolved_count=0, missing_abstract_count=0,
                complete=0, truncated=0, target_limit=limit, error_kind=None,
                error_message=None, expected_count=None, warning_kind=None,
                warning_message=None, started_at=now, updated_at=now)


def fail_state(state, kind, message):
    state.update(status="error", complete=0, truncated=0, error_kind=kind,
                 error_message=message, updated_at=utc_now())


def collect_direction(client, store, seed, direction, limit, *, page_size, ttl, refresh):
    seed_id = seed["paper_id"]
    state = store.get_state(seed_id, direction)
    cache_hit = False
    if state and not refresh:
        cache_hit = fresh(state["started_at"], ttl) and (
            (state["complete"] and state["raw_count"] <= limit)
            or (state["status"] == "truncated" and state["target_limit"] == limit)
        )
    if cache_hit:
        return {**state, "cache_hit": True, "network_raw_records": 0, "stop_run": False}

    # Resume only a recent, known cursor. Smaller quotas and refreshes start over.
    resume = (state and not refresh and fresh(state["started_at"], ttl)
              and state["next_offset"] is not None and state["raw_count"] <= limit
              and limit >= state["target_limit"])
    if not resume:
        state = new_state(seed_id, direction, limit)
    state.update(status="partial", complete=0, truncated=0, target_limit=limit,
                 error_kind=None, error_message=None, warning_kind=None, warning_message=None,
                 expected_count=seed.get("reference_count" if direction == "references" else "citation_count"),
                 updated_at=utc_now())
    with store.connection:
        store.save_state(state)
    network_rows = 0
    stop_run = False
    while state["raw_count"] < limit:
        offset = state["next_offset"]
        requested = min(page_size, limit - state["raw_count"])
        result = client.relation_page(seed_id, direction, offset, requested)
        if result["status"] != "ok":
            fail_state(state, result["error"]["kind"], result["error"]["message"])
            store.save_page(state, [])
            stop_run = result["stop_run"]
            break
        rows, next_offset, error, message = parse_page(result["response"], offset, requested)
        network_rows += len(rows)
        state["raw_count"] += len(rows)
        nested = "citedPaper" if direction == "references" else "citingPaper"
        for row in rows:
            paper = row.get(nested) if isinstance(row, dict) else None
            paper = paper if isinstance(paper, dict) else {}
            state["unresolved_count"] += not paper_id_valid(paper.get("paperId"))
            abstract = paper.get("abstract")
            state["missing_abstract_count"] += not isinstance(abstract, str) or not abstract.strip()
        state.update(next_offset=next_offset, updated_at=utc_now())
        expected = state["expected_count"]
        if error:
            fail_state(state, error, message)
        elif next_offset is None:
            state.update(status="complete", complete=1)
            if type(expected) is int and state["raw_count"] != expected:
                state.update(warning_kind="count_mismatch",
                             warning_message="API-reported end reached; returned record count differs from metadata.")
        elif state["raw_count"] >= limit:
            state.update(status="truncated", truncated=1)
        # Oversized responses are protocol failures: retain evidence/usable rows,
        # report actual count (never hide over-budget records), then stop the run.
        store.save_page(state, rows)
        if error == "response_exceeds_limit":
            stop_run = True
        if state["status"] != "partial":
            break
    else:
        state.update(status="truncated", truncated=1, updated_at=utc_now())
        store.save_page(state, [])
    return {**state, "cache_hit": False, "network_raw_records": network_rows, "stop_run": stop_run}


def collect(client, store, inputs, *, citation_budget=150, reference_cap=10000,
            page_size=100, ttl=DEFAULT_TTL, refresh=False):
    """Resolve 1–3 inputs, deduplicate seeds, then expand each seed only once.

    Quotas cover the whole cached/resumed sample, not an additional budget per
    invocation. Unused shares are not redistributed. Old DB observations remain.
    """
    if not 1 <= len(inputs) <= 3:
        raise ValueError("Provide 1 to 3 seed identifiers.")
    if type(citation_budget) is not int or citation_budget < 0:
        raise ValueError("citation_budget must be a nonnegative integer.")
    if type(reference_cap) is not int or reference_cap < 1:
        raise ValueError("reference_cap must be a positive integer.")
    if type(page_size) is not int or not 1 <= page_size <= 1000 or ttl < 0:
        raise ValueError("page_size must be in 1..1000; ttl must be nonnegative.")
    inputs = [input_id(normalize_alias(value)) for value in inputs]
    report = {"source": store.source, "status": "ok", "citation_budget": citation_budget,
              "inputs": [], "seeds": [], "network_citation_records": 0}
    seeds = {}
    stopped = None
    resolved_inputs = {}
    for value in inputs:
        paper = store.lookup(value)
        # Duplicate aliases resolved in this call need no second metadata request.
        trusted = paper and paper["alias_confirmed"] and not paper["alias_conflicted"]
        cached = trusted and not refresh and fresh(paper["metadata_fetched_at"], ttl)
        already_resolved = value in resolved_inputs or (
            trusted and value == normalize_alias(paper["paper_id"]) and paper["paper_id"] in seeds)
        if cached or already_resolved:
            paper = resolved_inputs.get(value, paper)
            report["inputs"].append({"input": value, "paper_id": paper["paper_id"],
                                     "status": "ok", "cache_hit": bool(cached)})
        elif stopped:
            report["inputs"].append({"input": value, "status": "skipped", "reason": stopped})
            continue
        else:
            result = client.metadata(value)
            body = result.get("response") or {}
            if result["status"] != "ok" or not paper_id_valid(body.get("paperId")):
                error = result.get("error", {"kind": "invalid_shape", "message": "Metadata has no usable paperId."})
                report["inputs"].append({"input": value, "status": "error", "error": error})
                if result.get("stop_run"):
                    stopped = error["kind"]
                continue
            if ":" not in value and normalize_alias(body["paperId"]) != value:
                report["inputs"].append({"input": value, "status": "error", "error": {
                    "kind": "canonical_id_conflict", "message": "Returned paperId differs from the requested canonical ID."}})
                continue
            previous_id = paper["paper_id"] if paper else None
            with store.connection:
                store.upsert_paper(body, utc_now(), metadata=True, alias=value)
            paper = store.lookup(value)
            report["inputs"].append({"input": value, "paper_id": paper["paper_id"],
                                     "status": "ok", "cache_hit": False})
            if previous_id and previous_id != paper["paper_id"]:
                report["inputs"][-1]["alias_reassigned_from"] = previous_id
        seeds[paper["paper_id"]] = paper
        resolved_inputs[value] = paper

    for index, paper in enumerate(seeds.values()):
        quota = citation_budget // len(seeds) + (index < citation_budget % len(seeds))
        entry = {"paper_id": paper["paper_id"], "citation_quota": quota}
        for direction, limit in (("references", reference_cap), ("citations", quota)):
            if stopped:
                entry[direction] = {"status": "skipped", "reason": stopped}
                continue
            result = collect_direction(client, store, paper, direction, limit,
                                       page_size=page_size, ttl=ttl, refresh=refresh)
            entry[direction] = result
            if direction == "citations":
                report["network_citation_records"] += result["network_raw_records"]
            if result["stop_run"]:
                stopped = result["error_kind"]
        report["seeds"].append(entry)
    statuses = [entry["status"] for entry in report["inputs"]]
    statuses += [seed[direction]["status"] for seed in report["seeds"] for direction in ("references", "citations")]
    if any(status in {"error", "skipped", "partial"} for status in statuses):
        report["status"] = "incomplete"
    elif "truncated" in statuses:
        report["status"] = "bounded"
    report["database_totals"] = store.counts()
    report["alias_conflicts"] = store.alias_conflicts()
    report["limitation"] = (
        "One-hop API-ordered sample; duplicates and unresolved rows consume quota. "
        "Database totals are cumulative unique IDs/edges, not this run's raw record count. "
        "Complete means API-reported end only; pagination is not a stable snapshot."
    )
    return redact(report, getattr(client, "api_key", None))
