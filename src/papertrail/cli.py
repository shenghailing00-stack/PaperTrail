"""CLI and a strictly offline, synthetic fixture transport."""

import argparse
import json
import os
from pathlib import Path
import sqlite3

from .api import S2Client, input_id, page_limit
from .collector import collect, DEFAULT_TTL
from .store import PaperStore, SOURCE, normalize_alias


class FixtureClient:
    """Same page boundary as HTTP; missing entries fail without live fallback."""
    api_key = None

    def __init__(self, path):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))

    def metadata(self, identifier):
        paper = self.data["metadata"].get(normalize_alias(identifier))
        if paper is None:
            return {"status": "error", "stop_run": False, "response": None,
                    "error": {"kind": "fixture_missing", "message": "Seed is absent from offline fixture."}}
        return {"status": "ok", "response": paper}

    def relation_page(self, paper_id, direction, offset, limit):
        rows = self.data["relations"].get(paper_id, {}).get(direction)
        if rows is None:
            return {"status": "error", "stop_run": False,
                    "error": {"kind": "fixture_missing", "message": "Relation is absent from offline fixture."}}
        body = {"offset": offset, "data": rows[offset:offset + limit]}
        if offset + limit < len(rows):
            body["next"] = offset + limit
        return {"status": "ok", "response": body}


def nonnegative(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a nonnegative integer.") from None
    if number < 0:
        raise argparse.ArgumentTypeError("Expected a nonnegative integer.")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seeds", nargs="+", type=lambda value: input_id(normalize_alias(value)))
    parser.add_argument("--db", required=True, help="Explicit SQLite path; use a separate DB for fixtures")
    parser.add_argument("--offline-fixture", type=Path, help="Never uses HTTP or reads the API key")
    parser.add_argument("--citation-budget", type=nonnegative, default=150)
    parser.add_argument("--reference-cap", type=nonnegative, default=10000)
    parser.add_argument("--page-size", type=page_limit, default=100)
    parser.add_argument("--cache-ttl-hours", type=nonnegative, default=DEFAULT_TTL // 3600)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= len(args.seeds) <= 3 or args.reference_cap == 0:
        parser.error("Provide 1..3 seeds and a positive reference cap.")
    client = None
    try:
        client = (FixtureClient(args.offline_fixture) if args.offline_fixture
                  else S2Client(os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None))
        source = "Synthetic offline fixture (Semantic Scholar schema)" if args.offline_fixture else SOURCE
        with PaperStore(args.db, source=source) as store:
            report = collect(client, store, args.seeds, citation_budget=args.citation_budget,
                             reference_cap=args.reference_cap, page_size=args.page_size,
                             ttl=args.cache_ttl_hours * 3600, refresh=args.refresh)
        report["mode"] = "offline_fixture" if args.offline_fixture else "api"
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 1 if report["status"] == "incomplete" else 0
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    except (sqlite3.Error, OSError):
        print("Local database/fixture access failed; check path, permissions and available disk space.")
        return 1
    except (ValueError, KeyError, TypeError):
        print("Invalid collection parameters or offline fixture structure.")
        return 1
    finally:
        if isinstance(client, S2Client):
            client.session.close()
