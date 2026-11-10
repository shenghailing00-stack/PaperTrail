"""Read one collection run as a directed graph; never collect or update data."""

import json
import re
import sqlite3

import networkx as nx

from .api import paper_id_valid
from .store import FIXTURE_SOURCE, SOURCE, PaperStore, ReadOnlyStoreError


LIMITATIONS = [
    "One-hop, API-ordered sample: relations between non-seed papers may be missing.",
    "Collection complete means API-reported end, not complete literature coverage; offset pagination is not a stable snapshot.",
    "Paper metadata is latest-known, not a historical attribute snapshot of this run.",
    "local_in_degree counts observed incoming sample edges; global_citation_count is the source's global count (synthetic in fixtures).",
    "Seed in-degree is bounded by the sampled citation quota; it is not comparable with non-seed in-degree.",
    "Neither local degree nor global citation count establishes quality, classic status or baseline status.",
    "Self-loops are retained: each adds one edge, one in-degree and one out-degree; weak connectivity is unchanged.",
]


class AnalysisError(ValueError):
    """The saved sample is missing, incomplete or inconsistent; no graph returned."""


def _require(condition, message):
    if not condition:
        raise AnalysisError(message)


def _count(value):
    return type(value) is int and value >= 0


def _validate_sample(sample):
    """Cross-check persisted completion evidence against get_sample's membership.

    Unresolved rows and missing metadata are coverage warnings, not pagination
    failures. They remain visible even when all directions reached their end.
    """
    _require(sample["status"] in ("ok", "bounded"),
             f"Run status is {sample['status']!r}; analysis requires ok or bounded, not incomplete/interrupted/running/error.")
    _require(sample["finished_at"], "Run has no completion timestamp.")
    report = sample["report"]
    _require(isinstance(report, dict) and report.get("status") in ("ok", "bounded"),
             "Missing or unsuccessful collection report; cannot establish collection scope.")
    _require(report["status"] == sample["status"], "Run status and collection report status disagree.")
    _require(all(report[key] == sample[key] for key in ("run_id", "source", "source_key")),
             "Run and collection report identity disagree.")
    ids = sample["paper_ids"]
    _require(ids, "Empty sample: no resolved papers to analyze.")
    _require(all(paper_id_valid(value) for value in ids),
             "Invalid/unresolved paper ID in sample; placeholders cannot become nodes.")
    members = set(ids)
    seeds = {seed["seed_id"]: seed["citation_quota"] for seed in sample["seeds"]}
    _require(seeds and len(seeds) == len(sample["seeds"]) and seeds.keys() <= members,
             "Invalid seed membership.")
    edges = set()
    for edge in sample["edges"]:
        _require(isinstance(edge, (list, tuple)) and len(edge) == 2
                 and all(paper_id_valid(endpoint) and endpoint in members for endpoint in edge),
                 "Citation edge endpoint is outside the current sample or unresolved.")
        edges.add(tuple(edge))

    config = sample["config"]
    for key, low, high in (("citation_budget", 0, 1000), ("reference_cap", 1, 10000),
                           ("request_budget", 1, 300)):
        _require(_count(config[key]) and low <= config[key] <= high,
                 "Invalid saved collection budget.")
    _require(all(_count(quota) for quota in seeds.values())
             and sum(seeds.values()) == config["citation_budget"],
             "Seed citation quotas disagree with the saved global budget.")
    attempts = report["request_attempts"]
    _require(_count(attempts) and attempts <= config["request_budget"]
             and report["request_budget"] == config["request_budget"]
             and report["citation_budget"] == config["citation_budget"],
             "Request/citation budget evidence is invalid or exceeded.")
    inputs = report["inputs"]
    _require(inputs and len(inputs) == len(sample["inputs"])
             and all(entry.get("status") == "ok" for entry in inputs)
             and {entry["paper_id"] for entry in inputs} == seeds.keys(),
             "Some seed inputs failed, were skipped, or disagree with membership.")
    reported_seeds = {seed["paper_id"]: seed for seed in report["seeds"]}
    _require(reported_seeds.keys() == seeds.keys() and len(reported_seeds) == len(report["seeds"]),
             "Collection report does not cover exactly the current seeds.")
    counts = {(row["seed_id"], row["direction"]): row for row in sample["record_counts"]}
    expected = {(seed, direction) for seed in seeds for direction in ("references", "citations")}
    _require(counts.keys() == expected and len(counts) == len(sample["record_counts"]),
             "Missing or duplicate direction counts.")
    warnings, direction_states = [], []
    for (seed, direction), row in counts.items():
        entry = reported_seeds[seed]
        state = entry[direction]
        limit = config["reference_cap"] if direction == "references" else seeds[seed]
        complete = (state.get("status") == "complete" and state.get("complete") == 1
                    and state.get("truncated") == 0 and state.get("next_offset") is None)
        truncated = (state.get("status") == "truncated" and state.get("truncated") == 1
                     and state.get("complete") == 0
                     and state.get("raw_count") == state.get("target_limit") == limit)
        _require((complete or truncated)
                 and not state.get("error_kind") and not state.get("error_message")
                 and not state.get("stop_run"),
                 "A collection direction is incomplete/failed or has invalid completion/truncation evidence.")
        _require(state["sample_run_id"] == sample["run_id"] and state["seed_id"] == seed
                 and state["direction"] == direction and entry["citation_quota"] == seeds[seed]
                 and _count(state["target_limit"]),
                 "Direction identity or budget disagrees with this run.")
        for key in ("raw_count", "unresolved_count", "missing_abstract_count"):
            _require(_count(row[key]) and row[key] == state[key],
                     "Saved record counts disagree with the collection report; check sample integrity.")
        # Complete caches may retain an older target_limit after budget changes.
        # Both the original limit and this run's actual quota must cover the rows.
        _require(row["unresolved_count"] <= row["raw_count"] <= min(limit, state["target_limit"])
                 and row["missing_abstract_count"] <= row["raw_count"],
                 "Record counts are invalid or exceed the collection budget.")
        direction_states.append({"seed_id": seed, "direction": direction, "status": state["status"],
                                 "raw_count": row["raw_count"], "target_limit": state["target_limit"],
                                 "expected_count": state.get("expected_count")})
        if truncated:
            warnings.append(f"{seed}/{direction} truncated at quota {limit} "
                            f"(source reports expected_count={state.get('expected_count')}); "
                            f"sample is API-ordered, not the most important {limit} papers.")
        if state.get("warning_kind"):
            warnings.append(f"{seed}/{direction}: {state['warning_kind']} "
                            f"(expected_count={state.get('expected_count')}, raw_count={row['raw_count']}).")

    has_truncation = any(state["status"] == "truncated" for state in direction_states)
    _require((report["status"] == "bounded") == has_truncation,
             "Collection status disagrees with directions: ok requires no truncation; bounded requires at least one truncated direction.")
    raw = sum(row["raw_count"] for row in counts.values())
    unresolved = sum(row["unresolved_count"] for row in counts.values())
    missing = sum(row["missing_abstract_count"] for row in counts.values())
    _require(report["sample_totals"] == {
        "papers": len(members), "citation_edges": len(edges),
        "raw_records": raw, "unresolved_records": unresolved,
    }, "Sample totals disagree with the collection report; check sample integrity.")
    _require(len(edges) <= raw - unresolved and len(members - seeds.keys()) <= raw - unresolved,
             "Unresolved records appear to have been converted to nodes or edges.")
    if unresolved:
        warnings.append(f"{unresolved} unresolved raw records have no paper ID; they create no nodes/edges.")
    if missing:
        warnings.append(f"{missing} raw records had missing abstracts at collection time (duplicates included).")
    if report.get("alias_conflicts"):
        warnings.append("Collection reported alias conflicts; graph membership uses canonical paper IDs only.")
    return {"raw_records": raw, "resolved_records": raw - unresolved,
            "unresolved_records": unresolved, "missing_abstract_records": missing,
            "duplicate_edge_observations": raw - unresolved - len(edges)}, warnings, direction_states


def load_graph(db_path, run_id):
    """Return a DiGraph from get_sample(run_id), using an enforced read-only DB.

    Completed and valid budget-truncated runs are accepted. All nodes, including isolated
    seeds, are added before edges. Metadata is read in the same SQLite snapshot.
    """
    _require(type(run_id) is int and 0 < run_id <= 2**63 - 1, "run_id must be a positive SQLite integer.")
    try:
        with PaperStore.open_readonly(db_path) as store:
            try:
                sample = store.get_sample(run_id)
            except ValueError as error:
                if str(error) == "Unknown collection run.":
                    raise AnalysisError("Unknown collection run; check run_id.") from None
                raise
            coverage, warnings, direction_states = _validate_sample(sample)
            truncated_directions = [
                {key: state[key] for key in ("seed_id", "direction", "raw_count", "target_limit", "expected_count")}
                for state in direction_states if state["status"] == "truncated"]
            identity = store.connection.execute(
                "SELECT source_key, source FROM dataset_identity WHERE singleton = 1").fetchone()
            _require(identity and (identity["source_key"], identity["source"]) ==
                     (sample["source_key"], sample["source"]), "Run and database source identity disagree.")
            if sample["source_key"] == "semantic-scholar" and sample["source"] == SOURCE:
                mode = "api"
            elif (re.fullmatch(r"fixture:[0-9a-f]{64}", sample["source_key"])
                  and sample["source"] == FIXTURE_SOURCE):
                mode = "offline_fixture"
                warnings.append("Synthetic offline fixture: these are not real papers or real citation counts.")
            else:
                raise AnalysisError("Unknown or inconsistent sample source; cannot distinguish real data from fixture.")
            graph = nx.DiGraph()
            seed_ids = {seed["seed_id"] for seed in sample["seeds"]}
            # Query metadata by exact membership; never read historical citation_edges.
            for paper_id in sample["paper_ids"]:
                row = store.connection.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
                _require(row is not None, "Sample member has no paper metadata row; check database integrity.")
                _require(row["source"] == sample["source"], "Paper metadata source disagrees with this sample.")
                attrs = {key: row[key] for key in (
                    "title", "year", "url", "abstract", "reference_count", "source", "updated_at", "metadata_fetched_at")}
                attrs.update(paperId=paper_id, global_citation_count=row["citation_count"],
                             authors=json.loads(row["authors_json"]) if row["authors_json"] else None,
                             externalIds=json.loads(row["external_ids_json"]) if row["external_ids_json"] else None,
                             is_seed=paper_id in seed_ids)
                graph.add_node(paper_id, **attrs)
            graph.add_edges_from(sample["edges"])
            graph.graph.update({key: sample[key] for key in (
                "run_id", "source", "source_key", "status", "started_at", "finished_at", "config", "seeds", "record_counts")})
            graph.graph.update(mode=mode, edge_direction="citing -> cited", metadata_policy="latest_known",
                               collection_scope="budget_truncated" if truncated_directions else "complete",
                               truncated_directions=truncated_directions, direction_states=direction_states,
                               self_loop_policy="retain", coverage=coverage, warnings=warnings,
                               limitations=list(LIMITATIONS))
            return graph
    except (AnalysisError, ReadOnlyStoreError):
        raise
    except (sqlite3.Error, ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise AnalysisError("Stored sample/schema is invalid or unreadable; check database integrity. No analysis produced.") from None


def summarize_graph(graph, *, top=5):
    """JSON-compatible metrics and deterministic candidates, without a quality score."""
    _require(type(top) is int and 1 <= top <= 100, "top must be an integer in 1..100.")
    nodes = [{**attrs, "local_in_degree": graph.in_degree(paper_id),
              "local_out_degree": graph.out_degree(paper_id)}
             for paper_id, attrs in sorted(graph.nodes(data=True))]
    candidates = sorted((node for node in nodes if node["local_in_degree"] > 0),
                        key=lambda node: (-node["local_in_degree"], node["paperId"]))[:top]
    candidates = [{key: node[key] for key in (
        "paperId", "title", "year", "url", "local_in_degree", "local_out_degree", "global_citation_count", "is_seed")}
        | {"reason": ("Seed paper; in-degree reflects sampled citing papers (bounded by quota). " if node["is_seed"] else "")
                     + f"Observed {node['local_in_degree']} unique incoming sample edges (self-loops included)."}
        for node in candidates]
    return {
        "sample": dict(graph.graph),
        "statistics": {"paper_count": graph.number_of_nodes(), "unique_edge_count": graph.number_of_edges(),
                       "weak_component_count": nx.number_weakly_connected_components(graph),
                       "isolated_node_count": nx.number_of_isolates(graph),
                       "self_loop_count": nx.number_of_selfloops(graph)},
        "nodes": nodes,
        "edges": [{"citing_paper_id": citing, "cited_paper_id": cited} for citing, cited in sorted(graph.edges)],
        "candidate_order": "local_in_degree descending, paperId ascending for ties; positive in-degree only; includes seeds",
        "candidates": candidates,
    }
