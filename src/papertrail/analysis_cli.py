"""Analyze one saved run without HTTP or writable database access."""

import json
import os
import sys

from .analysis import AnalysisError, load_graph, summarize_graph
from .api import redact
from .cli import SafeArgumentParser
from .store import ReadOnlyStoreError


def main(argv=None):
    # Keep the terminal/redirect's encoding; escape only unrepresentable characters.
    # In-memory streams (e.g. test captures) may not expose reconfigure.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Existing PaperTrail SQLite database (read-only)")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--top", type=int, default=5, help="1..100 candidates by sample in-degree (default 5)")
    parser.add_argument("--json", action="store_true", help="Print complete structured JSON to stdout")
    args = parser.parse_args(argv)
    if not 0 < args.run_id <= 2**63 - 1 or not 1 <= args.top <= 100:
        parser.error("--run-id must be a positive SQLite integer and --top must be in 1..100.")
    # Only used to redact output from old/external data; never passed to a client.
    secret = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None
    try:
        result = redact(summarize_graph(load_graph(args.db, args.run_id), top=args.top), secret)
    except (AnalysisError, ReadOnlyStoreError) as error:
        print(redact(str(error), secret), file=sys.stderr)
        return 1
    except OSError:
        print("Cannot access database; check path and read permissions.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    sample, stats = result["sample"], result["statistics"]
    print(f"Run {sample['run_id']} | {sample['status']} | {sample['mode']} | {sample['source']}")
    print(f"Source identity: {sample['source_key']}")
    print(f"Collection scope: {sample['collection_scope']} (not a complete literature network)")
    print("Truncated directions:")
    for direction in sample["truncated_directions"]:
        print(f"  {direction['seed_id']}/{direction['direction']} | raw_count={direction['raw_count']} | "
              f"target_limit={direction['target_limit']} | expected_count={direction['expected_count']}")
    if not sample["truncated_directions"]:
        print("  None: all directions reached the source-reported end.")
    print(f"Papers: {stats['paper_count']} | Unique edges: {stats['unique_edge_count']} | "
          f"Weak components: {stats['weak_component_count']} | Isolates: {stats['isolated_node_count']} | "
          f"Self-loops: {stats['self_loop_count']}")
    print("Coverage: " + json.dumps(sample["coverage"]))
    print("Candidates (" + result["candidate_order"] + "):")
    for node in result["candidates"]:
        marker = "[seed] " if node["is_seed"] else ""
        print(f"  {marker}{node['paperId']} | {node['title']} | local_in_degree={node['local_in_degree']} | "
              f"local_out_degree={node['local_out_degree']} | global_citation_count={node['global_citation_count']}")
        print("    " + node["reason"])
    if not result["candidates"]:
        print("  None: no observed incoming edges.")
    for warning in sample["warnings"]:
        print("Warning: " + warning)
    for limitation in sample["limitations"]:
        print("Limitation: " + limitation)
    return 0
