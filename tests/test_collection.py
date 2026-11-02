"""TASK-002A acceptance tests: all databases temporary, all HTTP forbidden/mocked."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest
import requests

from papertrail import api, cli, collector, store as store_module
from papertrail.collector import collect, new_state
from papertrail.store import PaperStore


A, B, C, D = (letter * 40 for letter in "abcd")
SEEDS = ["ARXIV:0000.00001", "ARXIV:0000.00002"]
FIXTURE = Path(__file__).parent / "fixtures" / "s2_collection.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("Real HTTP forbidden")))
    clock = [0.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))


@pytest.fixture
def store(tmp_path):
    with PaperStore(tmp_path / "test.db") as result:
        yield result


@pytest.fixture
def client():
    result = cli.FixtureClient(FIXTURE)
    result.metadata = Mock(wraps=result.metadata)
    result.relation_page = Mock(wraps=result.relation_page)
    return result


def edges(store):
    return {tuple(row) for row in store.connection.execute(
        "SELECT citing_paper_id, cited_paper_id FROM citation_edges")}


def response(body=None, status=200):
    result = Mock(status_code=status, headers={})
    result.json.return_value = body
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


@pytest.mark.parametrize("n", [1, 2])
def test_import_pages_directions_missing_fields_and_unique_counts(store, client, n):
    report = collect(client, store, SEEDS[:n], page_size=2)
    assert report["status"] == "ok"
    assert report["database_totals"] == {"papers": 4, "citation_edges": 3 if n == 1 else 5}
    assert {(A, C), (D, A), (B, A)} <= edges(store)
    if n == 2:
        assert {(B, C), (D, B)} <= edges(store)
    refs = report["seeds"][0]["references"]
    assert (refs["raw_count"], refs["unresolved_count"], refs["missing_abstract_count"]) == (3, 1, 2)
    assert refs["complete"] == 1 and refs["truncated"] == 0
    assert client.relation_page.call_args_list[1].args == (A, "references", 2, 2)
    shared = store.lookup(C)
    assert shared["abstract"] == "Example abstract."  # Null duplicate must not erase it.
    assert store.lookup(D)["abstract"] is None
    assert store.lookup(D)["metadata_fetched_at"] is None
    assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert all(call.args[0] in {A, B} for call in client.relation_page.call_args_list)


@pytest.mark.parametrize("n,budget,quotas", [(1, 150, [150]), (2, 150, [75, 75]),
                                            (3, 150, [50, 50, 50]), (3, 151, [51, 50, 50])])
def test_global_raw_budget_counts_duplicates_and_unresolved(store, client, n, budget, quotas):
    ids = [A, B, "e" * 40]
    inputs = SEEDS + ["ARXIV:0000.00003"]
    for identifier, seed in zip(inputs, ids):
        client.data["metadata"][identifier] = {"paperId": seed}
        client.data["relations"][seed] = {
            "references": [], "citations": [{"citingPaper": {"paperId": D}}, {"citingPaper": None}] * 100,
        }
    report = collect(client, store, inputs[:n], citation_budget=budget, page_size=31)
    assert [s["citation_quota"] for s in report["seeds"]] == quotas
    assert report["network_citation_records"] == budget
    assert sum(s["citations"]["raw_count"] for s in report["seeds"]) == budget
    assert all(s["citations"]["truncated"] and not s["citations"]["complete"] for s in report["seeds"])
    assert len(edges(store)) == n
    assert store.counts()["papers"] == n + 1
    client.metadata.reset_mock()
    client.relation_page.reset_mock()
    cached = collect(client, store, inputs[:n], citation_budget=budget)
    assert cached["status"] == "bounded" and cached["network_citation_records"] == 0
    client.metadata.assert_not_called()
    client.relation_page.assert_not_called()


def test_cached_reopen_and_aliases_skip_all_http(tmp_path, client):
    path = tmp_path / "persist.db"
    client.data["metadata"]["DOI:10.0000/demo-a"] = client.data["metadata"][SEEDS[0]]
    with PaperStore(path) as db:
        collect(client, db, [SEEDS[0], "DOI:10.0000/demo-a", SEEDS[1]], page_size=1)
        before = edges(db)
    client.metadata.reset_mock()
    client.relation_page.reset_mock()
    with PaperStore(path) as db:
        report = collect(client, db, ["doi:10.0000/DEMO-A", A, SEEDS[1]])
        assert len(report["seeds"]) == 2
        assert edges(db) == before and db.counts()["papers"] == 4
        assert all(seed[d]["cache_hit"] for seed in report["seeds"] for d in ("references", "citations"))
    client.metadata.assert_not_called()
    client.relation_page.assert_not_called()


def test_unknown_external_aliases_canonicalize_to_one_seed(store, client):
    alias = "DOI:10.0000/unknown-before-query"
    client.data["metadata"][alias] = copy.deepcopy(client.data["metadata"][SEEDS[0]])
    report = collect(client, store, [SEEDS[0], alias])
    assert len(report["seeds"]) == 1 and report["seeds"][0]["citation_quota"] == 150
    assert client.metadata.call_count == 2
    assert client.relation_page.call_count == 2
    assert store.lookup(alias)["paper_id"] == A


@pytest.mark.parametrize("kwargs", [{"refresh": True}, {"ttl": 0}])
def test_refresh_and_expiration_requery_without_duplicate_edges(store, client, kwargs):
    collect(client, store, SEEDS)
    before = edges(store)
    client.metadata.reset_mock()
    client.relation_page.reset_mock()
    report = collect(client, store, SEEDS, **kwargs)
    assert report["status"] == "ok" and edges(store) == before
    assert client.metadata.call_count == 2 and client.relation_page.call_count == 4


def test_candidate_metadata_is_not_a_fresh_seed_cache(store, client):
    collect(client, store, SEEDS[:1])
    assert store.lookup(B)["metadata_fetched_at"] is None
    client.metadata.reset_mock()
    collect(client, store, [B])
    client.metadata.assert_called_once_with(B)  # Fixture lacks that input: must still try metadata.


def test_reference_cap_then_resume_with_larger_limit(store, client):
    report = collect(client, store, SEEDS[:1], reference_cap=2)
    refs = report["seeds"][0]["references"]
    assert refs["status"] == "truncated" and refs["next_offset"] == 2
    client.relation_page.reset_mock()
    report = collect(client, store, SEEDS[:1], reference_cap=4)
    assert report["seeds"][0]["references"]["complete"]
    client.relation_page.assert_called_once_with(A, "references", 2, 2)


def test_smaller_citation_budget_restarts_sample_preserving_history(store, client):
    collect(client, store, SEEDS[:1])
    client.relation_page.reset_mock()
    report = collect(client, store, SEEDS[:1], citation_budget=1)
    assert report["seeds"][0]["citations"]["raw_count"] == 1
    assert report["network_citation_records"] == 1
    client.relation_page.assert_called_once_with(A, "citations", 0, 1)
    assert (B, A) in edges(store)  # Historical union; not falsely reported as current sample.


def test_zero_budget_never_requests_citations(store, client):
    report = collect(client, store, SEEDS, citation_budget=0)
    assert report["status"] == "bounded" and report["network_citation_records"] == 0
    assert all(call.args[1] == "references" for call in client.relation_page.call_args_list)
    assert all(s["citations"]["status"] == "truncated" for s in report["seeds"])


def test_resume_citations_shares_budget_with_other_cached_seed(store, client):
    for seed, identifier in zip((A, B), SEEDS):
        client.data["metadata"][identifier] = {"paperId": seed}
        client.data["relations"][seed] = {
            "references": [], "citations": [{"citingPaper": {"paperId": D}}] * 100,
        }
    real_page = client.relation_page._mock_wraps
    def interrupt_second_seed(seed, direction, offset, limit):
        if seed == B and direction == "citations" and offset == 30:
            return {"status": "error", "stop_run": True,
                    "error": {"kind": "rate_limit", "message": "Synthetic interruption."}}
        return real_page(seed, direction, offset, limit)
    client.relation_page.side_effect = interrupt_second_seed
    first = collect(client, store, SEEDS, page_size=30)
    assert first["status"] == "incomplete" and first["network_citation_records"] == 105
    client.relation_page.side_effect = None
    client.relation_page.reset_mock()
    resumed = collect(client, store, SEEDS, page_size=30)
    assert resumed["network_citation_records"] == 45
    assert sum(s["citations"]["raw_count"] for s in resumed["seeds"]) == 150
    assert resumed["seeds"][0]["citations"]["cache_hit"]
    assert client.relation_page.call_args_list[0].args == (B, "citations", 30, 30)
    assert all(call.args[0] == B for call in client.relation_page.call_args_list)


def test_failed_refresh_retains_previous_success_but_invalidates_complete_cache(store, client):
    collect(client, store, SEEDS[:1])
    before = edges(store)
    client.relation_page.side_effect = [{"status": "error", "stop_run": True,
        "error": {"kind": "ssl", "message": "Synthetic certificate failure."}}]
    report = collect(client, store, SEEDS[:1], refresh=True)
    assert report["status"] == "incomplete" and edges(store) == before
    state = store.get_state(A, "references")
    assert state["status"] == "error" and state["complete"] == 0 and state["next_offset"] == 0
    client.relation_page.side_effect = None
    client.relation_page.reset_mock()
    collect(client, store, SEEDS[:1])
    client.relation_page.assert_called_once_with(A, "references", 0, 100)


@pytest.mark.parametrize("failure,kind,attempts", [
    (429, "rate_limit", 2),
    (requests.exceptions.SSLError("certificate verify failed private-text"), "ssl", 1),
    (requests.exceptions.SSLError("unexpected EOF private-text"), "ssl", 3),
    (503, "server_error", 3),
])
def test_page_failure_preserves_data_and_resumes_after_reopen(tmp_path, monkeypatch, failure, kind, attempts):
    path = tmp_path / "resume.db"
    client = api.S2Client()
    error = response(status=failure) if isinstance(failure, int) else failure
    get = Mock(side_effect=[response({"paperId": A}),
                           response({"offset": 0, "next": 1, "data": [{"citedPaper": {"paperId": C}}]})]
                          + [error] * attempts)
    monkeypatch.setattr(client.session, "get", get)
    with PaperStore(path) as db:
        report = collect(client, db, SEEDS[:1], page_size=1)
        assert report["status"] == "incomplete" and edges(db) == {(A, C)}
        state = db.get_state(A, "references")
        assert state["status"] == "error" and state["error_kind"] == kind
        assert state["next_offset"] == 1 and state["raw_count"] == 1
        assert not state["complete"] and not state["truncated"]
        assert report["seeds"][0]["citations"]["status"] == "skipped"
        assert "private-text" not in json.dumps(report)
    assert get.call_count == 2 + attempts
    get = Mock(side_effect=[response({"offset": 1, "data": [{"citedPaper": {"paperId": D}}]}),
                           response({"data": []})])
    monkeypatch.setattr(client.session, "get", get)
    with PaperStore(path) as db:
        report = collect(client, db, SEEDS[:1], page_size=1)
        assert report["status"] == "ok" and edges(db) == {(A, C), (A, D)}
        assert get.call_args_list[0].kwargs["params"]["offset"] == 1
        assert db.get_state(A, "references")["raw_count"] == 2
    client.session.close()


@pytest.mark.parametrize("body,kind", [
    ({"data": None}, "invalid_shape"),
    ({}, "invalid_shape"),
    ({"data": [], "next": 1}, "invalid_pagination"),
    ({"data": [], "next": 0}, "invalid_pagination"),
    ({"data": [{"citedPaper": {"paperId": C}}], "next": None}, "invalid_pagination"),
    ({"data": [{"citedPaper": {"paperId": C}}], "next": 0}, "invalid_pagination"),
    ({"data": [{"citedPaper": {"paperId": C}}], "next": 5}, "invalid_pagination"),
    ({"offset": 1, "data": [{"citedPaper": {"paperId": C}}]}, "invalid_pagination"),
])
def test_malformed_pages_never_cached_as_complete(store, client, body, kind):
    client.relation_page.side_effect = [{"status": "ok", "response": body},
                                        {"status": "ok", "response": {"data": []}}]
    report = collect(client, store, SEEDS[:1])
    state = store.get_state(A, "references")
    assert report["status"] == "incomplete" and state["error_kind"] == kind
    assert not state["complete"]
    if body.get("data"):
        assert (A, C) in edges(store)


@pytest.mark.parametrize("actual", [39, 41])
def test_count_mismatch_is_complete_cached_and_cli_success(tmp_path, monkeypatch, capsys, client, actual):
    client.data["metadata"][SEEDS[0]].update(referenceCount=40, citationCount=40)
    client.data["relations"][A] = {
        "references": [{"citedPaper": {"paperId": C}}] * actual,
        "citations": [{"citingPaper": {"paperId": D}}] * actual,
    }
    monkeypatch.setattr(cli, "FixtureClient", lambda path: client)
    path = tmp_path / "mismatch.db"
    argv = [SEEDS[0], "--db", str(path), "--offline-fixture", str(FIXTURE), "--page-size", "20"]
    for run in (1, 2):
        assert cli.main(argv) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "ok"
        for direction in ("references", "citations"):
            result = report["seeds"][0][direction]
            assert result["status"] == "complete" and result["complete"] == 1
            assert result["expected_count"] == 40 and result["raw_count"] == actual
            assert result["warning_kind"] == "count_mismatch" and result["error_kind"] is None
            assert result["cache_hit"] == (run == 2)
        if run == 2:
            client.metadata.assert_not_called()
            client.relation_page.assert_not_called()
        client.metadata.reset_mock()
        client.relation_page.reset_mock()
    with PaperStore(path) as db:
        assert db.get_state(A, "references")["warning_kind"] == "count_mismatch"


def test_oversized_page_keeps_actual_count_and_stops(store, client):
    client.relation_page.side_effect = [{"status": "ok", "response": {"data": [
        {"citedPaper": {"paperId": C}}, {"citedPaper": {"paperId": D}},
    ]}}]
    report = collect(client, store, SEEDS[:1], page_size=1)
    state = store.get_state(A, "references")
    assert state["raw_count"] == 2 and state["error_kind"] == "response_exceeds_limit"
    assert report["status"] == "incomplete" and client.relation_page.call_count == 1
    assert edges(store) == {(A, C), (A, D)}


def test_empty_results_complete_without_fabricated_edges(store, client):
    client.data["metadata"][SEEDS[0]] = {"paperId": A, "referenceCount": 0, "citationCount": 0}
    client.data["relations"][A] = {"references": [], "citations": []}
    report = collect(client, store, SEEDS[:1])
    assert report["status"] == "ok" and store.counts() == {"papers": 1, "citation_edges": 0}


@pytest.mark.parametrize("paper", [{}, {"paperId": None}, {"paperId": " "}])
def test_missing_seed_id_never_stored_or_expanded(store, client, paper):
    client.data["metadata"][SEEDS[0]] = paper
    report = collect(client, store, SEEDS[:1])
    assert report["status"] == "incomplete" and store.counts()["papers"] == 0
    client.relation_page.assert_not_called()


def test_missing_first_seed_does_not_block_valid_second(store, client):
    report = collect(client, store, ["DOI:10.0000/missing", SEEDS[0]])
    assert report["status"] == "incomplete" and len(report["seeds"]) == 1
    assert report["seeds"][0]["citation_quota"] == 150


def test_page_transaction_rolls_back_papers_edges_and_cursor(store):
    with store.connection:
        store.upsert_paper({"paperId": A}, api.utc_now(), metadata=True)
        state = new_state(A, "references", 10)
        store.save_state(state)
        store.connection.execute("""
            CREATE TRIGGER fail_edge BEFORE INSERT ON citation_edges
            WHEN NEW.cited_paper_id = 'dddddddddddddddddddddddddddddddddddddddd'
            BEGIN SELECT RAISE(ABORT, 'test page transaction'); END;
        """)
    state.update(next_offset=2, raw_count=2)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_page(state, [{"citedPaper": {"paperId": C}}, {"citedPaper": {"paperId": D}}])
    assert store.counts() == {"papers": 1, "citation_edges": 0}
    assert store.get_state(A, "references")["next_offset"] == 0


@pytest.mark.parametrize("inputs,kwargs", [([], {}), (SEEDS * 2, {}), (["a title"], {}),
    (SEEDS, {"citation_budget": -1}), (SEEDS, {"reference_cap": 0}),
    (SEEDS, {"page_size": 1001}), (SEEDS, {"ttl": -1})])
def test_invalid_configuration_before_network(store, client, inputs, kwargs):
    with pytest.raises((ValueError, api.argparse.ArgumentTypeError)):
        collect(client, store, inputs, **kwargs)
    client.metadata.assert_not_called()
    client.relation_page.assert_not_called()


def test_offline_cli_and_error_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "not-read-in-fixture-mode")
    monkeypatch.setattr(requests, "Session", Mock(side_effect=AssertionError("No HTTP client allowed")))
    args = [*SEEDS, "--db", str(tmp_path / "demo.db"), "--offline-fixture", str(FIXTURE), "--page-size", "2"]
    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["mode"] == "offline_fixture" and first["source"].startswith("Synthetic")
    assert first["database_totals"] == {"papers": 4, "citation_edges": 5}
    assert cli.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["network_citation_records"] == 0
    assert cli.main(["DOI:10.0000/absent", *args[2:]]) == 1
    assert "not-read-in-fixture-mode" not in capsys.readouterr().out


def test_cli_bad_database_and_parameters(tmp_path, capsys):
    assert cli.main([SEEDS[0], "--db", str(tmp_path / "missing" / "test.db"),
                     "--offline-fixture", str(FIXTURE)]) == 1
    assert "access failed" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        cli.main([SEEDS[0], "--db", str(tmp_path / "test.db"), "--citation-budget", "-1"])
    assert error.value.code == 2


def test_external_alias_conflict_retains_mapping_and_requires_direct_confirmation(store, client):
    alias = "DOI:10.0000/shared"
    with store.connection:
        store.upsert_paper({"paperId": A}, api.utc_now(), metadata=True, alias=alias)
        store.upsert_paper({"paperId": B, "externalIds": {"DOI": "10.0000/shared"}},
                           api.utc_now(), metadata=True, alias=SEEDS[1])
    assert store.lookup(alias)["paper_id"] == A
    assert store.lookup(alias)["alias_conflicted"] == 1
    report = collect(client, store, [A, B])
    assert {seed["paper_id"] for seed in report["seeds"]} == {A, B}
    assert report["alias_conflicts"] == [{"alias": alias, "retained_paper_id": A}]
    # A successful direct query is evidence for reassigning this input alias only.
    client.data["metadata"][alias] = {"paperId": B}
    client.metadata.reset_mock()
    resolved = collect(client, store, [alias])
    client.metadata.assert_called_once_with(alias)
    assert resolved["inputs"][0]["alias_reassigned_from"] == A
    assert resolved["alias_conflicts"] == []
    assert store.lookup(alias)["paper_id"] == B
    assert store.lookup(A)["paper_id"] == A and store.lookup(B)["paper_id"] == B


def test_unconfirmed_external_alias_cannot_merge_distinct_input_seeds(store, client):
    # A's sidecar claims the external ID of B, before B has ever been imported.
    client.data["metadata"][SEEDS[0]]["externalIds"]["ArXiv"] = "0000.00002"
    report = collect(client, store, SEEDS)
    assert client.metadata.call_count == 2
    assert [seed["paper_id"] for seed in report["seeds"]] == [A, B]
    assert report["inputs"][1]["alias_reassigned_from"] == A
    assert store.lookup(SEEDS[1])["paper_id"] == B


def test_refresh_does_not_skip_a_previously_confirmed_alias(store, client):
    alias = "DOI:10.0000/changed"
    client.data["metadata"][alias] = {"paperId": A}
    collect(client, store, [SEEDS[0], alias])
    client.data["metadata"][alias] = {"paperId": B}
    client.metadata.reset_mock()
    report = collect(client, store, [SEEDS[0], alias], refresh=True)
    assert client.metadata.call_count == 2
    assert [seed["paper_id"] for seed in report["seeds"]] == [A, B]
    assert report["inputs"][1]["alias_reassigned_from"] == A


def test_canonical_id_cannot_be_reassigned(store, client):
    client.data["metadata"][A] = {"paperId": B}
    report = collect(client, store, [A])
    assert report["status"] == "incomplete"
    assert report["inputs"][0]["error"]["kind"] == "canonical_id_conflict"
    assert store.counts()["papers"] == 0
    client.relation_page.assert_not_called()


@pytest.mark.parametrize("empty", [None, "", " \t", [], {}])
def test_empty_metadata_does_not_overwrite_known_values(store, empty):
    original = {"paperId": A, "title": "Known title", "year": 2024, "abstract": "Known abstract",
                "url": "https://example.org/a", "authors": [{"name": "Author"}],
                "externalIds": {"DOI": "10.0000/a"}, "citationCount": 3, "referenceCount": 2}
    with store.connection:
        store.upsert_paper(original, api.utc_now(), metadata=True)
    before = store.lookup(A)
    with store.connection:
        store.upsert_paper({key: A if key == "paperId" else empty for key in original}, api.utc_now())
    after = store.lookup(A)
    for field in ("title", "year", "abstract", "url", "authors_json", "external_ids_json",
                  "citation_count", "reference_count", "metadata_fetched_at"):
        assert after[field] == before[field]
    with store.connection:
        store.upsert_paper({"paperId": A, "citationCount": 0, "referenceCount": 0}, api.utc_now())
    assert store.lookup(A)["citation_count"] == store.lookup(A)["reference_count"] == 0


@pytest.fixture
def wall_clock(monkeypatch):
    now = [datetime(2026, 11, 2, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0]
    monkeypatch.setattr(store_module, "datetime", Clock)
    monkeypatch.setattr(collector, "utc_now", lambda: now[0].isoformat())
    return now


@pytest.mark.parametrize("resumed_cap,final_status", [(2, "truncated"), (4, "complete")])
def test_positive_ttl_resume_does_not_rejuvenate_old_pages(store, client, wall_clock, resumed_cap, final_status):
    collect(client, store, SEEDS[:1], reference_cap=1, ttl=10)
    started = store.get_state(A, "references")["started_at"]
    wall_clock[0] += timedelta(seconds=9)
    client.relation_page.reset_mock()
    report = collect(client, store, SEEDS[:1], reference_cap=resumed_cap, ttl=10)
    client.relation_page.assert_called_once_with(A, "references", 1, resumed_cap - 1)
    state = report["seeds"][0]["references"]
    assert state["status"] == final_status and state["started_at"] == started
    assert state["updated_at"] == wall_clock[0].isoformat()
    wall_clock[0] += timedelta(seconds=2)  # Old prefix expired, completion only 2 seconds ago.
    client.relation_page.reset_mock()
    report = collect(client, store, SEEDS[:1], reference_cap=resumed_cap, ttl=10)
    assert not report["seeds"][0]["references"]["cache_hit"]
    assert client.relation_page.call_args_list[0].args == (A, "references", 0, resumed_cap)
    assert store.get_state(A, "references")["started_at"] == wall_clock[0].isoformat()


def test_expired_checkpoint_restarts_instead_of_resuming(store, client, wall_clock):
    collect(client, store, SEEDS[:1], reference_cap=1, ttl=10)
    wall_clock[0] += timedelta(seconds=10)
    client.relation_page.reset_mock()
    collect(client, store, SEEDS[:1], reference_cap=4, ttl=10)
    assert client.relation_page.call_args_list[0].args == (A, "references", 0, 4)


def test_smaller_budget_restarts_partial_prefix_even_when_it_fits(store, client):
    client.data["relations"][A]["citations"] = [{"citingPaper": {"paperId": D}}] * 20
    real_page = client.relation_page._mock_wraps
    def interrupted(seed, direction, offset, limit):
        if direction == "citations" and offset == 2:
            return {"status": "error", "stop_run": True,
                    "error": {"kind": "rate_limit", "message": "Synthetic interruption."}}
        return real_page(seed, direction, offset, limit)
    client.relation_page.side_effect = interrupted
    collect(client, store, SEEDS[:1], citation_budget=10, page_size=2)
    assert store.get_state(A, "citations")["raw_count"] == 2
    client.relation_page.side_effect = None
    client.relation_page.reset_mock()
    report = collect(client, store, SEEDS[:1], citation_budget=3, page_size=2)
    assert report["network_citation_records"] == 3
    assert report["seeds"][0]["citations"]["raw_count"] == 3
    assert [call.args for call in client.relation_page.call_args_list] == [
        (A, "citations", 0, 2), (A, "citations", 2, 1)]


def test_cli_report_redacts_key_in_inputs_errors_and_alias_conflicts(tmp_path, monkeypatch, capsys):
    secret = "synthetic-secret-marker"
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", secret)
    replies = [response({"paperId": A, "externalIds": {"DOI": f"10.0000/{secret}"}}),
               response({"data": [{"citedPaper": {"paperId": B,
                         "externalIds": {"DOI": f"10.0000/{secret}"}}}]}),
               requests.exceptions.SSLError(f"certificate verify failed {secret}")]
    monkeypatch.setattr(requests.Session, "get", Mock(side_effect=replies))
    identifier = f"ARXIV:{secret}"
    assert cli.main([identifier, "--db", str(tmp_path / "safe.db")]) == 1
    output = capsys.readouterr().out
    assert secret not in output and "[REDACTED]" in output
    report = json.loads(output)
    assert report["alias_conflicts"] and report["seeds"][0]["citations"]["error_kind"] == "ssl"


def test_legacy_database_migration_preserves_data_and_reconfirms_external_alias(tmp_path, client):
    path = tmp_path / "legacy.db"
    with PaperStore(path) as db:
        with db.connection:
            db.upsert_paper(client.data["metadata"][SEEDS[0]], api.utc_now(), metadata=True, alias=SEEDS[0])
            state = new_state(A, "references", 10000)
            state.update(status="error", error_kind="count_mismatch", next_offset=None)
            db.save_state(state)
    # Reproduce the original TASK-002A schema in this temporary test DB only.
    with sqlite3.connect(path) as db:
        for table, columns in (("paper_aliases", ("confirmed", "conflicted")),
                               ("fetch_state", ("expected_count", "warning_kind", "warning_message"))):
            for column in columns:
                db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    with PaperStore(path) as db:
        assert db.counts()["papers"] == 1
        assert db.lookup(A)["alias_confirmed"] == 1
        assert db.lookup(SEEDS[0])["alias_confirmed"] == 0
        assert db.get_state(A, "references")["status"] == "error"
        report = collect(client, db, SEEDS[:1])
        assert report["status"] == "ok"
        client.metadata.assert_called_once_with(SEEDS[0])
        assert db.lookup(SEEDS[0])["alias_confirmed"] == 1
    with PaperStore(path) as db:
        assert db.lookup(SEEDS[0])["alias_confirmed"] == 1  # Reopening migration is idempotent.
