"""Run membership, safe collection limits and dataset isolation; never live HTTP."""

import copy
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest
import requests

from papertrail import api, cli
from papertrail.collector import collect, new_state
from papertrail.store import FIXTURE_SOURCE, SOURCE, PaperStore, SourceMismatch


A, B, C, D, E = (letter * 40 for letter in "abcde")
SEEDS = ["ARXIV:0000.00001", "ARXIV:0000.00002"]
FIXTURE = Path(__file__).parent / "fixtures" / "s2_collection.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("Real HTTP forbidden")))
    clock = [0.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))


@pytest.fixture
def client():
    result = cli.FixtureClient(FIXTURE)
    result.metadata = Mock(wraps=result.metadata)
    result.relation_page = Mock(wraps=result.relation_page)
    return result


@pytest.fixture
def store(tmp_path):
    with PaperStore(tmp_path / "runs.db") as db:
        yield db


def response(body=None, status=200):
    result = Mock(status_code=status, headers={})
    result.json.return_value = body
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def test_exact_membership_raw_counts_and_source(store, client):
    report = collect(client, store, SEEDS, page_size=2)
    sample = store.get_sample(report["run_id"])
    assert sample["source"] == FIXTURE_SOURCE and sample["source_key"] == client.source_key
    assert sample["inputs"] == SEEDS and sample["config"]["citation_budget"] == 150
    assert sample["started_at"] and sample["finished_at"] and sample["status"] == "ok"
    assert sample["paper_ids"] == [A, B, C, D]
    assert {tuple(edge) for edge in sample["edges"]} == {(A, C), (B, C), (B, A), (D, A), (D, B)}
    assert sample["seeds"] == [{"seed_id": A, "citation_quota": 75}, {"seed_id": B, "citation_quota": 75}]
    assert report["sample_totals"] == {"papers": 4, "citation_edges": 5, "raw_records": 7, "unresolved_records": 1}
    refs = next(row for row in sample["record_counts"] if row["seed_id"] == A and row["direction"] == "references")
    assert (refs["raw_count"], refs["unresolved_count"], refs["missing_abstract_count"]) == (3, 1, 2)
    assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_cached_sample_survives_reopen_and_does_not_mutate_old_run(tmp_path, client):
    path = tmp_path / "cached.db"
    with PaperStore(path) as db:
        first = collect(client, db, SEEDS, page_size=1)
        original = db.get_sample(first["run_id"])
        original_items = [tuple(row) for row in db.connection.execute(
            "SELECT seed_id, direction, ordinal, other_paper_id, fetched_at FROM run_items WHERE run_id = ? ORDER BY rowid",
            (first["run_id"],))]
    client.metadata.reset_mock()
    client.relation_page.reset_mock()
    with PaperStore(path) as db:
        second = collect(client, db, SEEDS)
        new = db.get_sample(second["run_id"])
        assert new["run_id"] != original["run_id"]
        assert (new["paper_ids"], new["edges"], new["record_counts"]) == (
            original["paper_ids"], original["edges"], original["record_counts"])
        assert db.get_sample(first["run_id"]) == original
        items = [tuple(row) for row in db.connection.execute(
            "SELECT seed_id, direction, ordinal, other_paper_id, fetched_at FROM run_items WHERE run_id = ? ORDER BY rowid",
            (second["run_id"],))]
        assert sorted(items) == sorted(original_items)
        assert second["request_attempts"] == 0
    client.metadata.assert_not_called()
    client.relation_page.assert_not_called()


@pytest.mark.parametrize("budget", [0, 1])
def test_smaller_budget_excludes_historical_nodes_and_edges(store, client, budget):
    first = collect(client, store, SEEDS[:1])
    original = store.get_sample(first["run_id"])
    second = collect(client, store, SEEDS[:1], citation_budget=budget)
    small = store.get_sample(second["run_id"])
    assert [B, A] not in small["edges"] and B not in small["paper_ids"]
    assert small["edges"] == ([[A, C], [D, A]] if budget else [[A, C]])
    assert second["sample_totals"]["raw_records"] == 3 + budget  # Includes unresolved + duplicate refs.
    assert second["database_totals"] == first["database_totals"]
    assert store.get_sample(first["run_id"]) == original


def test_refresh_membership_changes_without_changing_previous_sample(store, client):
    first = collect(client, store, SEEDS[:1])
    original = store.get_sample(first["run_id"])
    client.data["relations"][A]["references"] = [{"citedPaper": {"paperId": E}}]
    second = collect(client, store, SEEDS[:1], refresh=True)
    sample = store.get_sample(second["run_id"])
    assert [A, E] in sample["edges"] and [A, C] not in sample["edges"]
    assert C not in sample["paper_ids"] and store.lookup(C) is not None
    assert store.get_sample(first["run_id"]) == original


def test_dropping_seed_does_not_induce_edges_from_history_between_sample_nodes(store, client):
    collect(client, store, SEEDS)
    second = collect(client, store, SEEDS[:1])
    sample = store.get_sample(second["run_id"])
    assert sample["paper_ids"] == [A, B, C, D]  # All old endpoints still occur in A's sample.
    assert {tuple(edge) for edge in sample["edges"]} == {(A, C), (B, A), (D, A)}
    assert second["sample_totals"]["citation_edges"] == 3
    assert second["database_totals"]["citation_edges"] == 5


def test_expanded_budget_copies_prefix_including_unresolved_records(store, client):
    first = collect(client, store, SEEDS[:1], reference_cap=2, citation_budget=1)
    old = store.get_sample(first["run_id"])
    client.relation_page.reset_mock()
    second = collect(client, store, SEEDS[:1], reference_cap=4, citation_budget=2)
    assert [call.args for call in client.relation_page.call_args_list] == [
        (A, "references", 2, 2), (A, "citations", 1, 1)]
    assert second["sample_totals"]["raw_records"] == 5
    assert second["sample_totals"]["unresolved_records"] == 1
    assert store.get_sample(first["run_id"]) == old


def test_failure_sample_does_not_borrow_skipped_historical_edges(store, client):
    collect(client, store, SEEDS[:1])
    original_page = client.relation_page._mock_wraps
    def fail_after_prefix(seed, direction, offset, limit):
        if direction == "references" and offset == 2:
            return {"status": "error", "stop_run": True,
                    "error": {"kind": "rate_limit", "message": "Synthetic failure."}}
        return original_page(seed, direction, offset, limit)
    client.relation_page.side_effect = fail_after_prefix
    failed = collect(client, store, SEEDS[:1], refresh=True, page_size=2)
    sample = store.get_sample(failed["run_id"])
    assert sample["status"] == "incomplete" and sample["paper_ids"] == [A, C]
    assert sample["edges"] == [[A, C]] and failed["sample_totals"]["raw_records"] == 2
    assert sample["report"]["seeds"][0]["citations"]["status"] == "skipped"
    client.relation_page.side_effect = None
    resumed = collect(client, store, SEEDS[:1], page_size=2)
    assert resumed["sample_totals"]["raw_records"] == 5
    assert store.get_sample(failed["run_id"]) == sample


def test_sql_failure_rolls_back_membership_and_marks_run_interrupted(store, client):
    with store.connection:
        store.connection.execute("""CREATE TRIGGER fail_citations BEFORE INSERT ON citation_edges
            WHEN NEW.citing_paper_id = 'dddddddddddddddddddddddddddddddddddddddd'
            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError):
        collect(client, store, SEEDS[:1])
    sample = store.get_sample(1)
    assert sample["status"] == "interrupted" and sample["finished_at"]
    assert sample["edges"] == [[A, C]] and sample["paper_ids"] == [A, C]
    assert store.lookup(D) is None
    state = store.get_state(A, "citations")
    assert state["raw_count"] == 0 and state["next_offset"] == 0
    assert store.has_sample(state)


@pytest.mark.parametrize("old_status", ["complete", "error"])
def test_full_legacy_schema_upgrade_refetches_membership_preserving_history(tmp_path, monkeypatch, old_status):
    path = tmp_path / "legacy.db"
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with PaperStore(path) as db:
        with db.connection:
            db.upsert_paper(data["metadata"][SEEDS[0]], api.utc_now(), metadata=True, alias=SEEDS[0])
        state = new_state(A, "references", 10000)
        state.update(status=old_status, complete=int(old_status == "complete"),
                     next_offset=None if old_status == "complete" else 1, raw_count=1)
        db.save_page(state, [{"citedPaper": {"paperId": E}}])
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE fetch_state DROP COLUMN sample_run_id")
        db.execute("ALTER TABLE fetch_state DROP COLUMN sample_digest")
        for table in ("run_items", "run_seeds", "collection_runs", "dataset_identity"):
            db.execute(f"DROP TABLE {table}")  # Only this temporary legacy test database.
    live = api.S2Client()
    get = Mock(side_effect=[response({"data": data["relations"][A]["references"]}),
                            response({"data": data["relations"][A]["citations"]})])
    monkeypatch.setattr(live.session, "get", get)
    try:
        with PaperStore(path) as db:
            assert db.get_state(A, "references")["sample_run_id"] is None
            assert db.lookup(E) is not None
            report = collect(live, db, SEEDS[:1])
            assert get.call_count == 2  # Metadata reused; legacy relation counters cannot imply membership.
            assert get.call_args_list[0].kwargs["params"]["offset"] == 0
            sample = db.get_sample(report["run_id"])
            assert [A, E] not in sample["edges"] and E not in sample["paper_ids"]
            assert db.lookup(E) is not None and db.counts()["citation_edges"] == 4
            again = collect(live, db, SEEDS[:1])
            assert again["request_attempts"] == 0 and get.call_count == 2
    finally:
        live.session.close()


@pytest.mark.parametrize("first_mode", ["fixture", "api"])
def test_real_and_fixture_identity_mismatch_before_requests(store, client, first_mode):
    live = api.S2Client()
    live.metadata = Mock(return_value={"status": "ok", "response": {"paperId": A}})
    live.relation_page = Mock(return_value={"status": "ok", "response": {"data": []}})
    first, second = (client, live) if first_mode == "fixture" else (live, client)
    try:
        collect(first, store, SEEDS[:1])
        before = store.counts()
        with pytest.raises(SourceMismatch):
            collect(second, store, SEEDS[:1])
        second.metadata.assert_not_called()
        second.relation_page.assert_not_called()
        assert store.counts() == before
        assert store.connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 1
    finally:
        live.session.close()


def test_different_fixture_content_cannot_reuse_cache(store, client, tmp_path):
    collect(client, store, SEEDS[:1])
    different = tmp_path / "different.json"
    data = copy.deepcopy(client.data)
    data["metadata"][SEEDS[0]]["title"] = "Different fixture"
    different.write_text(json.dumps(data), encoding="utf-8")
    other = cli.FixtureClient(different)
    other.metadata = Mock(wraps=other.metadata)
    with pytest.raises(SourceMismatch):
        collect(other, store, SEEDS[:1])
    other.metadata.assert_not_called()


@pytest.mark.parametrize("sources", [[FIXTURE_SOURCE], [SOURCE, FIXTURE_SOURCE], ["unknown"]])
def test_legacy_unknown_or_mixed_source_is_preserved_but_not_adopted(store, client, sources):
    with store.connection:
        for seed, source in zip((A, B), sources):
            store.source = source
            store.upsert_paper({"paperId": seed}, api.utc_now())
    with pytest.raises(SourceMismatch):
        collect(client, store, SEEDS[:1])
    assert store.counts()["papers"] == len(sources)
    client.metadata.assert_not_called()


@pytest.mark.parametrize("flag,value", [
    ("--citation-budget", "1001"), ("--reference-cap", "10001"), ("--page-size", "1001"),
    ("--request-budget", "301"), ("--request-budget", "0"), ("--cache-ttl-hours", "8761"),
    ("--cache-ttl-hours", "1" + "0" * 400),
])
def test_cli_limits_rejected_before_database_or_client(tmp_path, monkeypatch, flag, value):
    path = tmp_path / "should-not-exist.db"
    monkeypatch.setattr(requests, "Session", Mock(side_effect=AssertionError("No HTTP client allowed")))
    with pytest.raises(SystemExit) as error:
        cli.main([SEEDS[0], "--db", str(path), flag, value])
    assert error.value.code == 2 and not path.exists()


@pytest.mark.parametrize("limit", [1, 4])
def test_request_budget_counts_actual_http_attempts_including_retries(store, monkeypatch, limit):
    live = api.S2Client()
    get = Mock(side_effect=[response({"paperId": A}),
        response({"data": [{"citedPaper": {"paperId": C}}], "next": 1}),
        response(status=503), response(status=503)])
    monkeypatch.setattr(live.session, "get", get)
    try:
        report = collect(live, store, SEEDS[:1], page_size=1, request_budget=limit)
        assert get.call_count == report["request_attempts"] == limit
        assert report["status"] == "incomplete"
        assert report["seeds"][0]["references"]["error_kind"] == "request_budget"
        sample = store.get_sample(report["run_id"])
        assert sample["edges"] == ([[A, C]] if limit == 4 else [])
    finally:
        live.session.close()


def test_fixture_request_budget_preserves_prefix_for_next_run(store, client):
    first = collect(client, store, SEEDS[:1], page_size=1, request_budget=3)
    assert first["request_attempts"] == 3 and first["sample_totals"]["raw_records"] == 2
    assert first["status"] == "incomplete"
    client.relation_page.reset_mock()
    second = collect(client, store, SEEDS[:1], page_size=1)
    assert client.relation_page.call_args_list[0].args == (A, "references", 2, 1)
    assert second["sample_totals"]["raw_records"] == 5 and second["status"] == "ok"


def test_show_run_uses_persisted_identity_without_network(tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo # source.db"  # URI escaping must preserve the filename.
    assert cli.main([*SEEDS, "--db", str(path), "--offline-fixture", str(FIXTURE)]) == 0
    report = json.loads(capsys.readouterr().out)
    before = path.read_bytes()
    with sqlite3.connect(path) as db:
        contents = list(db.iterdump())
    real_connect = sqlite3.connect
    statements, opened = [], []
    def traced_connect(*args, **kwargs):
        opened.append((args, kwargs))
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    monkeypatch.setattr(PaperStore, "__init__", Mock(side_effect=AssertionError("Writable constructor forbidden")))
    monkeypatch.setattr(requests, "Session", Mock(side_effect=AssertionError("No HTTP client allowed")))
    assert cli.main(["--db", str(path), "--show-run", str(report["run_id"])]) == 0
    sample = json.loads(capsys.readouterr().out)
    assert sample["source"] == FIXTURE_SOURCE and len(sample["edges"]) == 5
    assert sample["report"]["source"] == FIXTURE_SOURCE
    assert path.read_bytes() == before
    assert len(opened) == 1 and opened[0][1]["uri"] is True
    assert opened[0][0][0].endswith("?mode=ro") and "immutable" not in opened[0][0][0]
    assert all(sql.lstrip().split()[0].upper() in {"SELECT", "PRAGMA", "BEGIN"} for sql in statements)
    with real_connect(path) as db:
        assert list(db.iterdump()) == contents
    with PaperStore.open_readonly(path) as reader:
        assert reader.get_sample(report["run_id"])["edges"] == sample["edges"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.connection.execute("UPDATE collection_runs SET status = 'changed'")
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind,message", [
    ("missing", "does not exist"), ("empty", "empty (0 bytes)"),
    ("unrelated", "Not a PaperTrail database"), ("legacy", "Old or incomplete PaperTrail schema"),
    ("invalid", "Cannot read SQLite database"),
])
def test_show_run_rejects_bad_databases_without_modification(tmp_path, capsys, kind, message):
    path = tmp_path / "input.db"
    if kind == "empty":
        path.touch()
    elif kind == "unrelated":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE notes (text TEXT)")
            db.execute("INSERT INTO notes VALUES ('keep this data')")
    elif kind == "legacy":
        with PaperStore(path):
            pass
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE fetch_state DROP COLUMN sample_run_id")
            db.execute("ALTER TABLE fetch_state DROP COLUMN sample_digest")
            for table in ("run_items", "run_seeds", "collection_runs", "dataset_identity"):
                db.execute(f"DROP TABLE {table}")
    elif kind == "invalid":
        path.write_bytes(b"not a sqlite database")
    before = path.read_bytes() if path.exists() else None
    assert cli.main(["--db", str(path), "--show-run", "1"]) == 1
    assert message in capsys.readouterr().out
    assert (path.read_bytes() if path.exists() else None) == before
    if kind == "unrelated":
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("notes",)]
            assert db.execute("SELECT text FROM notes").fetchone()[0] == "keep this data"


def test_readonly_reader_sees_wal_and_holds_consistent_snapshot(tmp_path, client):
    path = tmp_path / "wal.db"
    with PaperStore(path) as writer:
        assert writer.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        first = collect(client, writer, SEEDS[:1])
        with PaperStore.open_readonly(path) as reader:
            assert reader.get_sample(first["run_id"])["paper_ids"] == [A, B, C, D]
            second = collect(client, writer, SEEDS[:1])
            assert reader.connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 1
            with pytest.raises(ValueError, match="Unknown collection run"):
                reader.get_sample(second["run_id"])
        with PaperStore.open_readonly(path) as fresh_reader:
            assert fresh_reader.get_sample(second["run_id"])["status"] == "ok"


@pytest.mark.parametrize("change", [
    "DELETE FROM run_items WHERE direction='references' AND ordinal=1",
    "UPDATE run_items SET other_paper_id='dddddddddddddddddddddddddddddddddddddddd' WHERE direction='references' AND ordinal=0",
    "UPDATE run_items SET missing_abstract=1 WHERE direction='references' AND ordinal=0",
])
def test_tampered_membership_is_not_cached_or_resumed(store, client, change):
    collect(client, store, SEEDS[:1])
    with store.connection:
        store.connection.execute(change)
    assert not store.has_sample(store.get_state(A, "references"))
    client.relation_page.reset_mock()
    rebuilt = collect(client, store, SEEDS[:1])
    client.relation_page.assert_called_once_with(A, "references", 0, 100)
    assert not rebuilt["seeds"][0]["references"]["cache_hit"]
    assert rebuilt["sample_totals"]["raw_records"] == 5
    assert rebuilt["sample_totals"]["unresolved_records"] == 1
    assert store.has_sample(store.get_state(A, "references"))


@pytest.mark.parametrize("extra", [[SEEDS[0]], ["--offline-fixture", str(FIXTURE)], ["--refresh"]])
def test_show_run_rejects_collection_options_before_opening(tmp_path, monkeypatch, extra):
    path = tmp_path / "must-not-be-created.db"
    monkeypatch.setattr(PaperStore, "open_readonly", Mock(side_effect=AssertionError("Must not open DB")))
    with pytest.raises(SystemExit) as error:
        cli.main(["--db", str(path), "--show-run", "1", *extra])
    assert error.value.code == 2 and not path.exists()


@pytest.mark.parametrize("argument", ["invalid_run", "unknown_option"])
def test_cli_argument_errors_redact_configured_key(tmp_path, monkeypatch, capsys, argument):
    secret = "synthetic-argument-secret"
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", secret)
    extra = ["--show-run", secret] if argument == "invalid_run" else ["--" + secret]
    with pytest.raises(SystemExit) as error:
        cli.main(["--db", str(tmp_path / "unused.db"), *extra])
    output = capsys.readouterr()
    assert error.value.code == 2
    assert secret not in output.out + output.err and "[REDACTED]" in output.err
