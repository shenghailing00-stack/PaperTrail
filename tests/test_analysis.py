"""Graph behavior and read-only boundary, using only synthetic data/temp DBs."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import networkx as nx
import pytest
import requests

from papertrail import analysis, analysis_cli, cli
from papertrail.collector import collect
from papertrail.store import FIXTURE_SOURCE, SOURCE, PaperStore, ReadOnlyStoreError


A, B, C, D = (letter * 40 for letter in "abcd")
SEEDS = ["ARXIV:0000.00001", "ARXIV:0000.00002"]
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "s2_collection.json"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    forbidden = Mock(side_effect=AssertionError("No HTTP or HTTP client allowed"))
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(cli, "S2Client", forbidden)


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / "sample #1.db"
    client = cli.FixtureClient(FIXTURE)
    with PaperStore(path) as store:
        report = collect(client, store, SEEDS, page_size=2)
    return path, report["run_id"]


@pytest.fixture
def bounded_saved(tmp_path):
    path = tmp_path / "bounded.db"
    client = cli.FixtureClient(FIXTURE)
    client.data["metadata"][SEEDS[0]]["citationCount"] = 5
    client.data["relations"][A]["citations"] = [
        {"citingPaper": {"paperId": letter * 40}} for letter in "bdefg"]
    with PaperStore(path) as store:
        report = collect(client, store, SEEDS[:1], citation_budget=4, page_size=2)
    assert report["status"] == "bounded"
    return path, report["run_id"]


def change_report(path, run_id, change):
    with sqlite3.connect(path) as connection:
        report = json.loads(connection.execute(
            "SELECT report_json FROM collection_runs WHERE run_id = ?", (run_id,)).fetchone()[0])
        change(report)
        connection.execute("UPDATE collection_runs SET report_json = ? WHERE run_id = ?",
                           (json.dumps(report), run_id))


def test_direction_degrees_metadata_and_coverage(saved):
    graph = analysis.load_graph(*saved)
    assert type(graph) is nx.DiGraph
    assert set(graph.edges) == {(A, C), (B, C), (B, A), (D, A), (D, B)}
    result = analysis.summarize_graph(graph)
    assert result["statistics"] == dict(paper_count=4, unique_edge_count=5, weak_component_count=1,
                                        isolated_node_count=0, self_loop_count=0)
    nodes = {node["paperId"]: node for node in result["nodes"]}
    assert {key: (node["local_in_degree"], node["local_out_degree"]) for key, node in nodes.items()} == {
        A: (2, 1), B: (1, 2), C: (2, 0), D: (0, 2)}
    assert nodes[A]["title"] == "Synthetic seed A" and nodes[A]["year"] == 2024
    assert nodes[A]["global_citation_count"] == 2
    assert nodes[C]["global_citation_count"] is None and nodes[C]["year"] is None
    assert all("url" in node and "source" in node for node in nodes.values())
    assert result["sample"]["coverage"] == dict(raw_records=7, resolved_records=6,
        unresolved_records=1, missing_abstract_records=6, duplicate_edge_observations=1)
    assert result["sample"]["mode"] == "offline_fixture"
    assert result["sample"]["source"] == FIXTURE_SOURCE
    assert [node["paperId"] for node in result["candidates"]] == [A, C, B]
    assert [node["is_seed"] for node in result["candidates"]] == [True, False, True]
    assert [node["reason"].startswith("Seed paper;") for node in result["candidates"]] == [True, False, True]
    assert graph.graph["collection_scope"] == "complete" and graph.graph["truncated_directions"] == []
    assert {(state["seed_id"], state["direction"]): state["expected_count"]
            for state in graph.graph["direction_states"]} == {
        (A, "references"): 3, (A, "citations"): 2, (B, "references"): 1, (B, "citations"): 1}
    assert all("incoming sample edges" in node["reason"] for node in result["candidates"])
    assert any("unresolved" in warning for warning in graph.graph["warnings"])
    assert set(graph) == {A, B, C, D}  # The null-ID row is not a placeholder node.


def test_runs_do_not_induce_historical_edges_even_between_current_members(saved):
    path, first = saved
    with PaperStore(path) as store:
        second = collect(cli.FixtureClient(FIXTURE), store, SEEDS[:1])["run_id"]
        assert store.counts()["citation_edges"] == 5
    original, subset = analysis.load_graph(path, first), analysis.load_graph(path, second)
    assert set(original) == set(subset) == {A, B, C, D}
    assert set(subset.edges) == {(A, C), (B, A), (D, A)}
    assert (B, C) in original.edges and (B, C) not in subset.edges
    assert (D, B) in original.edges and (D, B) not in subset.edges


def test_duplicate_observations_and_self_loops_are_counted_once(tmp_path):
    client = cli.FixtureClient(FIXTURE)
    client.data["relations"][A] = {
        "references": [{"citedPaper": {"paperId": A}}] * 2,
        "citations": [{"citingPaper": {"paperId": A}}],
    }
    path = tmp_path / "loops.db"
    with PaperStore(path) as store:
        run = collect(client, store, SEEDS[:1])["run_id"]
    graph = analysis.load_graph(path, run)
    result = analysis.summarize_graph(graph)
    assert list(graph.edges) == [(A, A)]
    assert (graph.in_degree(A), graph.out_degree(A)) == (1, 1)
    assert result["statistics"] == dict(paper_count=1, unique_edge_count=1, weak_component_count=1,
                                        isolated_node_count=0, self_loop_count=1)
    assert graph.graph["coverage"]["duplicate_edge_observations"] == 2
    assert graph.graph["self_loop_policy"] == "retain"
    assert any("count_mismatch" in warning for warning in graph.graph["warnings"])


def test_isolated_seeds_and_weak_components(tmp_path):
    client = cli.FixtureClient(FIXTURE)
    client.data["metadata"][SEEDS[0]].pop("referenceCount")
    client.data["relations"][A] = {"references": [], "citations": []}
    client.data["relations"][B] = {"references": [], "citations": []}
    path = tmp_path / "isolates.db"
    with PaperStore(path) as store:
        run = collect(client, store, SEEDS)["run_id"]
    graph = analysis.load_graph(path, run)
    result = analysis.summarize_graph(graph)
    assert set(graph) == {A, B}
    assert result["statistics"] == dict(paper_count=2, unique_edge_count=0, weak_component_count=2,
                                        isolated_node_count=2, self_loop_count=0)
    assert result["candidates"] == []
    assert next(state for state in graph.graph["direction_states"]
                if (state["seed_id"], state["direction"]) == (A, "references"))["expected_count"] is None
    assert all(graph.in_degree(node) == graph.out_degree(node) == 0 for node in graph)


def test_metadata_is_latest_known_and_global_count_does_not_rank(saved):
    path, run = saved
    with PaperStore(path) as store:
        with store.connection:
            store.upsert_paper({"paperId": B, "title": "Updated title", "year": 2026,
                                "url": "https://example.org/b", "citationCount": 9999}, "2026-11-10")
    graph = analysis.load_graph(path, run)
    result = analysis.summarize_graph(graph)
    assert graph.nodes[B]["title"] == "Updated title"
    assert graph.nodes[B]["url"] == "https://example.org/b"
    assert graph.nodes[B]["global_citation_count"] == 9999
    assert graph.graph["metadata_policy"] == "latest_known"
    assert [node["paperId"] for node in result["candidates"]] == [A, C, B]


def test_completed_cache_with_changed_budgets_is_accepted(saved):
    path, _ = saved
    with PaperStore(path) as store:
        report = collect(cli.FixtureClient(FIXTURE), store, SEEDS, citation_budget=10, reference_cap=20)
        assert report["request_attempts"] == 0
        assert report["seeds"][0]["citations"]["target_limit"] == 75  # Original cache budget.
    assert analysis.load_graph(path, report["run_id"]).number_of_edges() == 5


@pytest.mark.parametrize("options, status", [({"citation_budget": 0}, "bounded"),
    ({"reference_cap": 1}, "bounded"), ({"request_budget": 1}, "incomplete")])
def test_real_collector_budget_truncation_and_failure(tmp_path, options, status):
    path = tmp_path / "partial.db"
    with PaperStore(path) as store:
        report = collect(cli.FixtureClient(FIXTURE), store, SEEDS, **options)
    assert report["status"] == status
    if status == "incomplete":
        with pytest.raises(analysis.AnalysisError, match="Run status"):
            analysis.load_graph(path, report["run_id"])
    else:
        graph = analysis.load_graph(path, report["run_id"])
        assert graph.graph["collection_scope"] == "budget_truncated"
        truncated = graph.graph["truncated_directions"]
        assert truncated and all(state["raw_count"] == state["target_limit"] for state in truncated)
        assert any("truncated at quota" in warning for warning in graph.graph["warnings"])
        if "reference_cap" in options:
            assert truncated == [{"seed_id": A, "direction": "references", "raw_count": 1,
                                  "target_limit": 1, "expected_count": 3}]
        else:
            assert all(state["direction"] == "citations" and state["raw_count"] == 0 for state in truncated)


def test_bounded_citation_graph_metrics_and_cli(bounded_saved, capsys):
    path, run = bounded_saved
    before = path.read_bytes()
    graph = analysis.load_graph(path, run)
    result = analysis.summarize_graph(graph)
    assert graph.graph["collection_scope"] == "budget_truncated"
    assert graph.graph["truncated_directions"] == [
        {"seed_id": A, "direction": "citations", "raw_count": 4, "target_limit": 4, "expected_count": 5}]
    assert graph.number_of_nodes() == 6 and graph.number_of_edges() == 5
    assert set(graph.edges) == {(A, C)} | {(letter * 40, A) for letter in "bdef"}
    assert (graph.in_degree(A), graph.out_degree(A)) == (4, 1)
    assert (graph.in_degree(C), graph.out_degree(C)) == (1, 0)
    assert all((graph.in_degree(letter * 40), graph.out_degree(letter * 40)) == (0, 1) for letter in "bdef")
    assert "g" * 40 not in graph
    assert any("truncated at quota 4" in warning and "expected_count=5" in warning
               and "not the most important 4" in warning for warning in graph.graph["warnings"])
    assert any("not comparable with non-seed" in message for message in graph.graph["limitations"])
    args = ["--db", str(path), "--run-id", str(run)]
    assert analysis_cli.main(args) == 0
    output = capsys.readouterr().out
    assert "Collection scope: budget_truncated (not a complete literature network)" in output
    assert f"{A}/citations | raw_count=4 | target_limit=4 | expected_count=5" in output
    assert f"[seed] {A}" in output and "Seed paper;" in output
    assert "truncated at quota 4" in output
    assert analysis_cli.main(args + ["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == result
    assert path.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"raw_count": 3}, {"target_limit": 5}, {"complete": 1}, {"truncated": 0},
    {"error_kind": "request_budget"}, {"error_message": "Failure"}, {"stop_run": True},
])
def test_invalid_truncated_direction_rejected(bounded_saved, changes):
    change_report(*bounded_saved, lambda report: report["seeds"][0]["citations"].update(changes))
    with pytest.raises(analysis.AnalysisError, match="direction is incomplete"):
        analysis.load_graph(*bounded_saved)


@pytest.mark.parametrize("run_status, report_status, message", [
    ("ok", "ok", "ok requires no truncation"),
    ("ok", "bounded", "status disagree"),
    ("bounded", "ok", "status disagree"),
])
def test_truncation_status_consistency(bounded_saved, run_status, report_status, message):
    path, run = bounded_saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE collection_runs SET status = ? WHERE run_id = ?", (run_status, run))
    change_report(path, run, lambda report: report.update(status=report_status))
    with pytest.raises(analysis.AnalysisError, match=message):
        analysis.load_graph(path, run)


def test_bounded_without_any_truncated_direction_rejected(saved):
    path, run = saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE collection_runs SET status = 'bounded' WHERE run_id = ?", (run,))
    change_report(path, run, lambda report: report.update(status="bounded"))
    with pytest.raises(analysis.AnalysisError, match="bounded requires at least one truncated direction"):
        analysis.load_graph(path, run)


@pytest.mark.parametrize("status", ["incomplete", "interrupted", "running", "bounded", "error", "mystery"])
def test_invalid_or_inconsistent_run_status_rejected(saved, status):
    path, run = saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE collection_runs SET status = ? WHERE run_id = ?", (status, run))
    with pytest.raises(analysis.AnalysisError, match="Run status"):
        analysis.load_graph(path, run)


@pytest.mark.parametrize("changes", [
    {"status": "skipped"}, {"status": "error"}, {"status": "partial"}, {"complete": 0}, {"truncated": 1},
    {"next_offset": 3}, {"error_kind": "request_budget"}, {"stop_run": True},
])
def test_ok_label_cannot_hide_direction_failure(saved, changes):
    path, run = saved
    change_report(path, run, lambda report: report["seeds"][0]["references"].update(changes))
    with pytest.raises(analysis.AnalysisError, match="direction is incomplete"):
        analysis.load_graph(path, run)


@pytest.mark.parametrize("change, message", [
    (lambda r: r.update(request_attempts=101), "budget"),
    (lambda r: r.update(request_budget=99), "budget"),
    (lambda r: r["seeds"][0]["references"].update(target_limit=1), "budget"),
    (lambda r: r["seeds"][0]["references"].update(raw_count=999), "record counts"),
    (lambda r: r["seeds"].pop(), "current seeds"),
    (lambda r: r["inputs"][0].update(status="error"), "seed inputs"),
    (lambda r: r.update(status="incomplete"), "collection report"),
    (lambda r: r.update(source="Unknown"), "identity"),
])
def test_inconsistent_report_rejected(saved, change, message):
    change_report(*saved, change)
    with pytest.raises(analysis.AnalysisError, match=message):
        analysis.load_graph(*saved)


@pytest.mark.parametrize("sql, message", [
    ("UPDATE collection_runs SET report_json = NULL", "collection report"),
    ("UPDATE collection_runs SET report_json = '{}'", "collection report"),
    ("UPDATE collection_runs SET report_json = 'invalid'", "invalid or unreadable"),
    ("UPDATE collection_runs SET finished_at = NULL", "completion timestamp"),
    ("UPDATE dataset_identity SET source_key = 'unknown'", "source identity"),
    ("UPDATE papers SET source = 'unknown'", "metadata source"),
    ("DELETE FROM papers WHERE paper_id = '" + C + "'", "no paper metadata"),
    ("DELETE FROM run_items WHERE other_paper_id IS NULL", "record counts"),
    ("UPDATE run_items SET other_paper_id = 'invented' WHERE other_paper_id IS NULL", "record counts"),
])
def test_inconsistent_database_rejected(saved, sql, message):
    with sqlite3.connect(saved[0]) as connection:
        connection.execute(sql)
    with pytest.raises(analysis.AnalysisError, match=message):
        analysis.load_graph(*saved)


@pytest.mark.parametrize("key, value, message", [
    ("paper_ids", [], "Empty sample"),
    ("paper_ids", [A, B, C, D, None], "unresolved paper ID"),
    ("edges", [[A, "outside"]], "endpoint"),
    ("edges", [[None, A]], "endpoint"),
])
def test_membership_contract_checked_before_networkx(saved, monkeypatch, key, value, message):
    original = PaperStore.get_sample
    def corrupted_sample(store, run):
        sample = original(store, run)
        sample[key] = value
        return sample
    monkeypatch.setattr(PaperStore, "get_sample", corrupted_sample)
    with pytest.raises(analysis.AnalysisError, match=message):
        analysis.load_graph(*saved)


def test_missing_run_and_missing_database(saved, tmp_path):
    with pytest.raises(analysis.AnalysisError, match="Unknown collection run"):
        analysis.load_graph(saved[0], 999)
    missing = tmp_path / "absent.db"
    with pytest.raises(ReadOnlyStoreError, match="does not exist"):
        analysis.load_graph(missing, 1)
    assert not missing.exists()


@pytest.mark.parametrize("kind", ["empty", "unrelated", "old", "not_sqlite"])
def test_unusable_databases_are_not_created_or_migrated(tmp_path, kind, capsys):
    path = tmp_path / "bad.db"
    if kind == "empty":
        path.touch()
    elif kind == "not_sqlite":
        path.write_text("not a database")
    else:
        with sqlite3.connect(path) as connection:
            tables = ["notes"] if kind == "unrelated" else ["papers", "citation_edges", "paper_aliases", "fetch_state"]
            for table in tables:
                connection.execute(f"CREATE TABLE {table} (id TEXT)")
    before = path.read_bytes()
    assert analysis_cli.main(["--db", str(path), "--run-id", "1", "--json"]) == 1
    output = capsys.readouterr()
    assert not output.out and output.err and path.read_bytes() == before


def test_analysis_enforces_readonly_and_never_reads_historical_edges(saved, monkeypatch):
    path, run = saved
    before = path.read_bytes()
    with sqlite3.connect(path) as connection:
        dump = list(connection.iterdump())
    original_connect = sqlite3.connect
    statements = []
    def readonly_connect(database_uri, **kwargs):
        assert str(database_uri).endswith("?mode=ro") and kwargs["uri"] is True
        connection = original_connect(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE collection_runs SET status = 'changed'")
        connection.rollback()  # The rejected UPDATE still opened an implicit transaction.
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == "citation_edges":
                raise AssertionError("Historical edges must not be read")
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                          sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        connection.set_trace_callback(statements.append)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(PaperStore, "__init__", Mock(side_effect=AssertionError("No writable constructor")))
        patch.setattr(sqlite3, "connect", readonly_connect)
        assert analysis.load_graph(path, run).number_of_edges() == 5
    assert statements and all(sql.lstrip().split()[0] in {"BEGIN", "SELECT", "PRAGMA"} for sql in statements)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert list(connection.iterdump()) == dump


def test_real_source_labels_remain_distinct_without_network(saved):
    # Only tests source-label handling, not authenticity or real API behavior.
    path, run = saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE dataset_identity SET source_key = 'semantic-scholar', source = ?", (SOURCE,))
        connection.execute("UPDATE collection_runs SET source_key = 'semantic-scholar', source = ?", (SOURCE,))
        connection.execute("UPDATE papers SET source = ?", (SOURCE,))
    change_report(path, run, lambda report: report.update(source=SOURCE, source_key="semantic-scholar"))
    assert analysis.load_graph(path, run).graph["mode"] == "api"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE dataset_identity SET source_key = 'unknown'")
        connection.execute("UPDATE collection_runs SET source_key = 'unknown'")
    change_report(path, run, lambda report: report.update(source_key="unknown"))
    with pytest.raises(analysis.AnalysisError, match="Unknown or inconsistent sample source"):
        analysis.load_graph(path, run)


def test_cli_summary_json_and_subprocess_entry(saved, capsys):
    args = ["--db", str(saved[0]), "--run-id", str(saved[1]), "--top", "2"]
    assert analysis_cli.main(args) == 0
    summary = capsys.readouterr().out
    assert "Papers: 4 | Unique edges: 5" in summary and "Synthetic offline fixture" in summary
    assert "local_in_degree=2" in summary and "quality" in summary
    assert "Collection scope: complete (not a complete literature network)" in summary
    assert f"[seed] {A}" in summary and f"[seed] {C}" not in summary
    assert analysis_cli.main(args + ["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["candidates"]) == 2 and len(result["nodes"]) == 4 and len(result["edges"]) == 5
    process = subprocess.run([sys.executable, str(ROOT / "scripts" / "analyze.py"), *args, "--json"],
                             capture_output=True, text=True, timeout=20, cwd=ROOT)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == result


def test_cli_redacts_old_metadata_without_changing_db(saved, monkeypatch, capsys):
    path, run = saved
    secret = "fake-test-secret-not-a-key"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE papers SET title = ? WHERE paper_id = ?", (secret, A))
    before = path.read_bytes()
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", secret)
    assert analysis_cli.main(["--db", str(path), "--run-id", str(run), "--json"]) == 0
    output = capsys.readouterr().out
    assert secret not in output and "[REDACTED]" in output
    assert path.read_bytes() == before
    with pytest.raises(SystemExit) as error:
        analysis_cli.main(["--db", str(path), "--run-id", secret])
    assert error.value.code == 2 and secret not in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--run-id", "0"], ["--run-id", str(2**64)],
                                  ["--run-id", "1", "--top", "0"], ["--run-id", "1", "--top", "101"]])
def test_invalid_cli_parameters_fail_before_opening_db(monkeypatch, args):
    monkeypatch.setattr(PaperStore, "open_readonly", Mock(side_effect=AssertionError("Must not open DB")))
    with pytest.raises(SystemExit) as error:
        analysis_cli.main(["--db", "unused.db", *args])
    assert error.value.code == 2


def test_empty_stored_membership_rejected(saved):
    with sqlite3.connect(saved[0]) as connection:
        connection.execute("DELETE FROM run_items")
        connection.execute("DELETE FROM run_seeds")
    with pytest.raises(analysis.AnalysisError, match="Empty sample"):
        analysis.load_graph(*saved)


def test_wal_metadata_and_membership_share_one_read_snapshot(saved, monkeypatch):
    path, run = saved
    original = PaperStore.get_sample
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode = WAL")
        def read_then_update(store, run_id):
            sample = original(store, run_id)
            writer.execute("UPDATE papers SET title = 'Later commit' WHERE paper_id = ?", (A,))
            writer.commit()
            return sample
        with monkeypatch.context() as patch:
            patch.setattr(PaperStore, "get_sample", read_then_update)
            graph = analysis.load_graph(path, run)
        assert graph.nodes[A]["title"] == "Synthetic seed A"
        assert analysis.load_graph(path, run).nodes[A]["title"] == "Later commit"


@pytest.mark.parametrize("encoding", [None, "cp936", "utf-8"])
@pytest.mark.parametrize("as_json", [False, True])
def test_unicode_titles_with_redirected_stdout(saved, tmp_path, encoding, as_json):
    path, run = saved
    title = "中文论文 Schrödinger ő 🧪"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE papers SET title = ? WHERE paper_id = ?", (title, A))
    before = path.read_bytes()
    env = os.environ.copy()
    env.pop("PYTHONIOENCODING", None)  # The user should not need this workaround.
    env["PYTHONUTF8"] = "0"
    command = [sys.executable]
    if encoding is not None:
        # Exercise legacy and UTF-8 streams regardless of the host's locale.
        command += ["-c", "import runpy,sys; sys.stdout.reconfigure(encoding=" + repr(encoding)
                    + "); runpy.run_path('scripts/analyze.py', run_name='__main__')"]
    else:
        command += [str(ROOT / "scripts" / "analyze.py")]
    command += ["--db", str(path), "--run-id", str(run)]
    if as_json:
        command.append("--json")
    redirected = tmp_path / "stdout.txt"
    with redirected.open("wb") as output:
        process = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, env=env, cwd=ROOT, timeout=20)
    assert process.returncode == 0, process.stderr
    assert not process.stderr and path.read_bytes() == before
    content = redirected.read_bytes()
    if as_json:
        data = json.loads(content.decode("ascii"))
        assert next(node for node in data["nodes"] if node["paperId"] == A)["title"] == title
    elif encoding is not None:
        assert title.encode(encoding, errors="backslashreplace") in content
    else:
        assert b"Schr" in content and b"Collection scope:" in content
