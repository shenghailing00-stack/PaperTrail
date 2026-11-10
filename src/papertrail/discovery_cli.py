"""Discover candidate papers in one saved run, entirely offline and read-only."""

import json
import os
import sys

from .analysis import AnalysisError
from .api import redact
from .cli import SafeArgumentParser
from .discovery import CATEGORIES, SORT_FIELDS, discover, validate_options
from .store import ReadOnlyStoreError


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Existing database (read-only)")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--year-min", type=int)
    parser.add_argument("--year-max", type=int)
    parser.add_argument("--citation-min", type=int)
    parser.add_argument("--citation-max", type=int)
    parser.add_argument("--keyword", action="append", default=[], help="Repeat for AND; literal title/abstract substring")
    parser.add_argument("--category", choices=CATEGORIES, action="append", default=[], help="Repeat for OR; excludes seeds")
    parser.add_argument("--include-seeds", action="store_true", help="Show matching seeds in a separate list")
    parser.add_argument("--sort-by", choices=SORT_FIELDS, default=SORT_FIELDS[0])
    parser.add_argument("--top", type=int, default=5, help="1..100 per group; default: 5")
    parser.add_argument("--json", action="store_true", help="Structured JSON including full-sample statistics; rows follow --top")
    args = parser.parse_args(argv)
    options = dict(year_min=args.year_min, year_max=args.year_max, citation_min=args.citation_min,
                   citation_max=args.citation_max, keywords=args.keyword, categories=args.category,
                   include_seeds=args.include_seeds, sort_by=args.sort_by, top=args.top)
    try:
        if not 0 < args.run_id <= 2**63 - 1:
            raise ValueError("--run-id must be a positive SQLite integer.")
        validate_options(**options)
    except ValueError as error:
        parser.error(str(error))
    secret = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None
    try:
        result = redact(discover(args.db, args.run_id, **options), secret)
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
    print(f"Current collected sample | Run {sample['run_id']} | {sample['status']} | {sample['source']}")
    print(f"Source identity: {sample['source_key']}")
    print(f"Collection scope: {sample['collection_scope']} (not a complete literature network)")
    print("Metadata policy: latest_known (not a historical snapshot)")
    print("Truncated directions: " + json.dumps(sample["truncated_directions"], ensure_ascii=True))
    print(f"Before filters: papers={stats['paper_count']} | seeds={stats['seed_count']} | non_seeds={stats['non_seed_count']}")
    print("Candidate counts (may overlap): " + json.dumps(stats["candidate_counts"]))
    print("Year distribution: " + json.dumps(stats["year_distribution"]))
    print("Missing metadata: " + json.dumps(stats["missing_metadata"]))
    print("Keyword paper counts: " + json.dumps(stats["keyword_counts"], ensure_ascii=True))
    print("Selection: " + json.dumps(result["selection"], ensure_ascii=True))
    print("Ranking: " + json.dumps(result["ranking"]))
    for group in ("candidates", "seeds"):
        if group == "seeds" and not args.include_seeds:
            continue
        count = result["selection"]["candidate_count_before_top" if group == "candidates" else "seed_count_before_top"]
        print(f"{group.capitalize()} (showing {len(result[group])} of {count} matching papers):")
        for row in result[group]:
            print(f"  {row['paperId']} | {row['title']} | year={row['year']} | "
                  f"global_citation_count={row['global_citation_count']} | "
                  f"referenced_by_seeds={row['referenced_by_seed_count']} | cites_seeds={row['cites_seed_count']} | "
                  f"local_in_degree={row['local_in_degree']} | local_out_degree={row['local_out_degree']} | "
                  f"rank={row['rank']} | sort_value={row['sort_value']} | tie_count={row['tie_count']}")
            print("    " + row["reason"])
        if not result[group]:
            print("  None matching the selection.")
    for warning in sample["warnings"]:
        print("Warning: " + warning)
    for limitation in sample["limitations"]:
        print("Limitation: " + limitation)
    return 0
