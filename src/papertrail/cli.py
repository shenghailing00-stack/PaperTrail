"""CLI and a strictly offline, synthetic fixture transport."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .api import S2Client, budget_error, input_id, page_limit, redact
from .collector import collect, validate_options, DEFAULT_TTL, DEFAULT_REQUEST_BUDGET
from .store import PaperStore, FIXTURE_SOURCE, ReadOnlyStoreError, SourceMismatch, normalize_alias


class FixtureClient:
    """Same page boundary as HTTP; missing entries fail without live fallback."""
    api_key = None
    source = FIXTURE_SOURCE

    def __init__(self, path):
        content = Path(path).read_bytes()
        self.source_key = "fixture:" + hashlib.sha256(content).hexdigest()
        self.data = json.loads(content)
        self.budget = None

    def metadata(self, identifier):
        if self.budget is not None and not self.budget.take():
            return budget_error()
        paper = self.data["metadata"].get(normalize_alias(identifier))
        if paper is None:
            return {"status": "error", "stop_run": False, "response": None,
                    "error": {"kind": "fixture_missing", "message": "Seed is absent from offline fixture."}}
        return {"status": "ok", "response": paper}

    def relation_page(self, paper_id, direction, offset, limit):
        if self.budget is not None and not self.budget.take():
            return budget_error()
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


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may echo an invalid value or an unrecognized argument.
        secret = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None
        super().error(redact(message, secret))


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("seeds", nargs="*", type=lambda value: input_id(normalize_alias(value)))
    parser.add_argument("--db", required=True, help="Explicit SQLite path; use a separate DB for fixtures")
    parser.add_argument("--show-run", type=int, help="Read a stored sample by run ID without HTTP")
    parser.add_argument("--offline-fixture", type=Path, help="Never uses HTTP or reads the API key")
    parser.add_argument("--citation-budget", type=nonnegative, default=150)
    parser.add_argument("--reference-cap", type=nonnegative, default=10000)
    parser.add_argument("--page-size", type=page_limit, default=100)
    parser.add_argument("--cache-ttl-hours", type=nonnegative, default=DEFAULT_TTL // 3600)
    parser.add_argument("--request-budget", type=nonnegative, default=DEFAULT_REQUEST_BUDGET,
                        help="1..300 actual request attempts including retries (default 100)")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if args.show_run is not None:
        if args.show_run < 1 or args.seeds or args.offline_fixture or args.refresh:
            parser.error("--show-run needs a positive run ID, without seeds/fixture/refresh.")
        try:
            with PaperStore.open_readonly(args.db) as store:
                sample = store.get_sample(args.show_run)
            secret = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None
            sample = redact(sample, secret)
            print(json.dumps(sample, ensure_ascii=True, indent=2))
            return 0
        except ReadOnlyStoreError as error:
            print(str(error))
            return 1
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            print("Run ID was not found, or the stored sample could not be read; check the run ID and database integrity.")
            return 1
    try:
        validate_options(args.seeds, args.citation_budget, args.reference_cap, args.page_size,
                         args.cache_ttl_hours * 3600, args.request_budget)
    except ValueError as error:
        parser.error(str(error))
    client = None
    try:
        client = (FixtureClient(args.offline_fixture) if args.offline_fixture
                  else S2Client(os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None))
        with PaperStore(args.db, source=client.source) as store:
            report = collect(client, store, args.seeds, citation_budget=args.citation_budget,
                             reference_cap=args.reference_cap, page_size=args.page_size,
                             ttl=args.cache_ttl_hours * 3600, refresh=args.refresh, request_budget=args.request_budget)
        report["mode"] = "offline_fixture" if args.offline_fixture else "api"
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 1 if report["status"] == "incomplete" else 0
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    except SourceMismatch as error:
        print(str(error))
        return 1
    except (sqlite3.Error, OSError):
        print("Local database/fixture access failed; check path, permissions and available disk space.")
        return 1
    except (ValueError, KeyError, TypeError):
        print("Invalid collection parameters or offline fixture structure.")
        return 1
    finally:
        if isinstance(client, S2Client):
            client.session.close()
