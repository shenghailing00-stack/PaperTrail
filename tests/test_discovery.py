"""TASK-003 acceptance: synthetic fixtures, temporary SQLite, no real HTTP."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest
import requests

from papertrail import analysis, cli, discovery, discovery_cli
from papertrail.collector import collect
from papertrail.store import PaperStore


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/s2_collection.json"
SEEDS = ["ARXIV:0000.00001", "ARXIV:0000.00002"]
A, B, C, D, E, F, G = (letter * 40 for letter in "abcdefg")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    forbidden = Mock(side_effect=AssertionError("No HTTP or HTTP client allowed"))
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(cli, "S2Client", forbidden)


@pytest.fixture
def fixture_path(tmp_path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    papers = {
        C: dict(paperId=C, title="Graph Foundations", abstract="Graph METHODS graph.", year=2020, citationCount=50),
        D: dict(paperId=D, title="Followup", abstract="Retrieval GRAPH methods", year=2023, citationCount=50),
        E: dict(paperId=E, title="Bridge Graph", abstract=None, year=2022, citationCount=0),
        F: dict(paperId=F, title="Unknown reference", abstract="retrieval", year=None, citationCount=None),
        G: dict(paperId=G, title=None, abstract=None, year=2024, citationCount=None),
    }
    data["relations"] = {
        A: {"references": [{"citedPaper": papers[p]} for p in (C, C, E, F)] + [{"citedPaper": {"paperId": None}}],
            "citations": [{"citingPaper": papers[p]} for p in (D, E, G)]},
        B: {"references": [{"citedPaper": papers[p]} for p in (C, E)],
            "citations": [{"citingPaper": papers[D]}]},
    }
    for seed, seed_id in zip(SEEDS, (A, B)):
        data["metadata"][seed].update(referenceCount=len(data["relations"][seed_id]["references"]),
                                      citationCount=len(data["relations"][seed_id]["citations"]))
    path = tmp_path / "discovery-fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def saved(tmp_path, fixture_path):
    path = tmp_path / "discovery #sample.db"
    with PaperStore(path) as store:
        report = collect(cli.FixtureClient(fixture_path), store, SEEDS, page_size=2)
    assert report["status"] == "ok"
    return path, report["run_id"]


def ids(rows):
    return [row["paperId"] for row in rows]


def test_categories_distinct_seed_counts_and_checkable_evidence(saved):
    result = discovery.discover(*saved)
    rows = {row["paperId"]: row for row in result["candidates"]}
    assert set(rows) == {C, D, E, F, G} and result["seeds"] == []
    assert rows[C]["referenced_by_seed_ids"] == [A, B]
    assert rows[C]["referenced_by_seed_count"] == 2  # Repeated A -> C counts once.
    assert (rows[C]["local_in_degree"], rows[C]["local_out_degree"]) == (2, 0)
    assert rows[D]["cites_seed_ids"] == [A, B]
    assert rows[D]["cites_seed_count"] == 2
    assert (rows[D]["local_in_degree"], rows[D]["local_out_degree"]) == (0, 2)
    assert rows[E]["categories"] == ["shared_reference", "shared_citing"]
    assert rows[F]["categories"] == ["shared_reference"]
    assert rows[G]["categories"] == ["shared_citing"]
    assert rows[C]["evidence"] == [{"kind": "shared_reference", "seed_ids": [A, B],
                                     "seed_count": 2, "edge_direction": "seed -> paper"}]
    assert rows[D]["evidence"][0]["edge_direction"] == "paper -> seed"
    assert all(row["reason"] and row["source"] and row["updated_at"] for row in rows.values())
    assert all("url" in row and "externalIds" in row and "metadata_fetched_at" in row for row in rows.values())
    assert result["sample"]["metadata_policy"] == "latest_known"
    assert json.loads(json.dumps(result, allow_nan=False)) == result


def test_neighbor_degree_is_not_a_seed_count(saved):
    # Pure helper test: an extra observed non-seed neighbor changes degree only.
    graph = analysis.load_graph(*saved)
    graph.add_edge(D, C)
    rows = {row["paperId"]: row for row in discovery.discover_papers(graph)}
    assert (rows[C]["local_in_degree"], rows[C]["referenced_by_seed_count"]) == (3, 2)
    assert (rows[D]["local_out_degree"], rows[D]["cites_seed_count"]) == (3, 2)


def test_seed_to_seed_citations_do_not_inflate_non_seed_candidates(tmp_path):
    # Existing fixture has B -> A, observed through A's citations. Both are seeds.
    path = tmp_path / "seed-relations.db"
    with PaperStore(path) as store:
        report = collect(cli.FixtureClient(FIXTURE), store, SEEDS, page_size=2)
    graph = analysis.load_graph(path, report["run_id"])
    assert graph.has_edge(B, A)
    result = discovery.discover(path, report["run_id"], include_seeds=True)
    assert set(ids(result["seeds"])) == {A, B}
    assert set(ids(result["candidates"])) == {C, D}
    assert result["statistics"]["candidate_count"] == 2
    assert result["statistics"]["candidate_counts"] == {"shared_reference": 1, "shared_citing": 1}
    assert all(row["categories"] == [] for row in result["seeds"])
    seeds = {row["paperId"]: row for row in result["seeds"]}
    assert seeds[A]["referenced_by_seed_ids"] == [B]
    assert seeds[B]["cites_seed_ids"] == [A]


@pytest.mark.parametrize("options, expected", [
    ({"year_min": 2022, "year_max": 2023}, [E, D]),
    ({"year_max": 2020}, [C]),
    ({"year_min": 2024}, [G]),
    ({"citation_min": 50, "citation_max": 50}, [C, D]),
    ({"citation_max": 0}, [E]),
    ({"citation_min": 0}, [C, E, D]),
    ({"keywords": [" gRaPh "]}, [C, E, D]),
    ({"keywords": ["graph", "METHODS"]}, [C, D]),
    ({"keywords": ["retrieval"]}, [F, D]),
    ({"keywords": ["graph methods"]}, [C, D]),
    ({"keywords": ["graph.*"]}, []),
    ({"categories": ["shared_citing"]}, [E, D, G]),
    ({"categories": ["shared_reference"]}, [C, E, F]),
    ({"categories": ["shared_reference", "shared_citing"]}, [C, E, F, D, G]),
    ({"year_min": 2021, "citation_min": 1, "keywords": ["graph"], "categories": ["shared_citing"]}, [D]),
])
def test_composable_filters_and_null_rules(saved, options, expected):
    result = discovery.discover(*saved, **options)
    assert ids(result["candidates"]) == expected
    assert result["statistics"]["paper_count"] == 7


def test_seed_inclusion_is_explicit_separate_and_filterable(saved):
    result = discovery.discover(*saved, include_seeds=True, sort_by="year")
    assert ids(result["seeds"]) == [B, A]
    assert not any(row["is_seed"] for row in result["candidates"])
    assert all(row["categories"] == [] and row["evidence"][0]["kind"] == "seed" for row in result["seeds"])
    assert ids(discovery.discover(*saved, include_seeds=True, year_max=2024)["seeds"]) == [A]
    assert discovery.discover(*saved, include_seeds=True, categories=["shared_reference"])["seeds"] == []
    with pytest.raises(ValueError, match="separately"):
        discovery.rank_papers(result["candidates"] + result["seeds"])


@pytest.mark.parametrize("sort_by, expected, ranks, ties", [
    ("referenced_by_seed_count", [C, E, F, D, G], [1, 1, 3, 4, 4], [2, 2, 1, 2, 2]),
    ("cites_seed_count", [D, E, G, C, F], [1, 2, 2, 4, 4], [1, 2, 2, 2, 2]),
    ("global_citation_count", [C, D, E, F, G], [1, 1, 3, None, None], [2, 2, 1, 0, 0]),
    ("year", [G, D, E, C, F], [1, 2, 3, 4, None], [1, 1, 1, 1, 0]),
])
def test_sorting_and_ties_are_stable(saved, sort_by, expected, ranks, ties):
    rows = discovery.discover(*saved, sort_by=sort_by)["candidates"]
    assert ids(rows) == expected
    assert [row["rank"] for row in rows] == ranks
    assert [row["tie_count"] for row in rows] == ties
    assert discovery.rank_papers(reversed(rows), sort_by=sort_by) == rows
    first = discovery.discover(*saved, sort_by=sort_by, top=1)["candidates"][0]
    assert first == rows[0]  # tie_count sees candidates omitted by top.


def test_statistics_count_unique_papers_overlap_missing_and_keywords(saved):
    result = discovery.discover(*saved, keywords=["graph", "GRAPH", "retrieval"], year_min=2099)
    stats = result["statistics"]
    assert result["candidates"] == [] and stats["scope"] == "current_run_before_filters"
    assert (stats["paper_count"], stats["seed_count"], stats["non_seed_count"], stats["candidate_count"]) == (7, 2, 5, 5)
    assert stats["candidate_counts"] == {"shared_reference": 3, "shared_citing": 3}
    assert stats["both_category_count"] == 1
    assert stats["year_distribution"] == [dict(year=y, paper_count=n) for y, n in ((2020, 1), (2022, 1), (2023, 1), (2024, 2), (2025, 1))]
    assert stats["missing_metadata"] == dict(year=1, title=1, abstract=4, global_citation_count=2, url=7, authors=7, externalIds=5)
    assert stats["keyword_counts"] == [
        dict(keyword="graph", title_paper_count=2, abstract_paper_count=2, either_paper_count=3),
        dict(keyword="retrieval", title_paper_count=0, abstract_paper_count=2, either_paper_count=2)]
    assert sum(row["paper_count"] for row in stats["year_distribution"]) + stats["missing_metadata"]["year"] == 7


def test_pure_helpers_do_not_mutate_graph_or_rows(saved):
    graph = analysis.load_graph(*saved)
    before = json.dumps(analysis.summarize_graph(graph), sort_keys=True)
    result = discovery.summarize_discovery(graph)
    result["sample"]["warnings"].append("caller edit")
    result["candidates"][0]["categories"].clear()
    assert json.dumps(analysis.summarize_graph(graph), sort_keys=True) == before
    rows = discovery.discover_papers(graph)
    before = json.dumps(rows)
    filtered = discovery.filter_papers(rows)
    filtered[0]["evidence"].clear()
    discovery.rank_papers(discovery.filter_papers(rows))
    assert json.dumps(rows) == before


def test_runs_are_isolated_even_when_all_nodes_are_the_same(tmp_path):
    path = tmp_path / "runs.db"
    with PaperStore(path) as store:
        first = collect(cli.FixtureClient(FIXTURE), store, SEEDS)["run_id"]
        second = collect(cli.FixtureClient(FIXTURE), store, SEEDS[:1])["run_id"]
    one, two = discovery.discover(path, first), discovery.discover(path, second)
    assert one["statistics"]["paper_count"] == two["statistics"]["paper_count"] == 4
    old = {row["paperId"]: row for row in one["candidates"]}
    new = {row["paperId"]: row for row in two["candidates"]}
    assert old[C]["referenced_by_seed_ids"] == [A, B] and new[C]["referenced_by_seed_ids"] == [A]
    assert old[D]["cites_seed_ids"] == [A, B] and new[D]["cites_seed_ids"] == [A]
    assert B not in old and new[B]["categories"] == ["shared_citing"]
    assert discovery.discover(path, first) == one


def test_bounded_sample_preserves_scope_and_warnings(saved, fixture_path):
    path, _ = saved
    with PaperStore(path) as store:
        run = collect(cli.FixtureClient(fixture_path), store, SEEDS, citation_budget=2)["run_id"]
    result = discovery.discover(path, run)
    assert result["sample"]["status"] == "bounded"
    assert result["sample"]["collection_scope"] == "budget_truncated"
    assert result["sample"]["truncated_directions"] == [dict(seed_id=A, direction="citations", raw_count=1, target_limit=1, expected_count=3)]
    assert any("truncated" in warning for warning in result["sample"]["warnings"])
    rows = {row["paperId"]: row for row in result["candidates"]}
    assert G not in rows and rows[E]["categories"] == ["shared_reference"]


@pytest.mark.parametrize("status", ["incomplete", "interrupted", "running", "error", "unknown"])
def test_failed_samples_rejected_without_partial_json(saved, status, capsys):
    path, run = saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE collection_runs SET status = ? WHERE run_id = ?", (status, run))
    with pytest.raises(analysis.AnalysisError):
        discovery.discover(path, run)
    assert discovery_cli.main(["--db", str(path), "--run-id", str(run), "--json"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "Run status" in output.err


def test_inconsistent_sample_rejected(saved):
    path, run = saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE run_items SET other_paper_id = NULL WHERE other_paper_id = ?", (C,))
    with pytest.raises(analysis.AnalysisError):
        discovery.discover(path, run)


def test_analysis_is_completely_readonly_and_never_reads_historical_edges(saved, monkeypatch, capsys):
    path, run = saved
    before = path.read_bytes()
    with sqlite3.connect(path) as connection:
        dump = list(connection.iterdump())
    original_connect = sqlite3.connect
    denied_reads, statements = [], []
    def readonly_connect(database_uri, **kwargs):
        assert str(database_uri).endswith("?mode=ro") and kwargs["uri"] is True
        connection = original_connect(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE papers SET title = 'forbidden'")
        connection.rollback()
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == "citation_edges":
                denied_reads.append(table)
                return sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                          sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_DROP_TABLE}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        connection.set_trace_callback(statements.append)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(PaperStore, "__init__", Mock(side_effect=AssertionError("No writable store")))
        patch.setattr(sqlite3, "connect", readonly_connect)
        assert discovery.discover(path, run)["statistics"]["paper_count"] == 7
        assert discovery_cli.main(["--db", str(path), "--run-id", str(run), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["statistics"]["paper_count"] == 7
    assert not denied_reads and statements
    assert all(sql.lstrip().split()[0] in {"BEGIN", "SELECT", "PRAGMA"} for sql in statements)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert list(connection.iterdump()) == dump


def test_seed_only_sample_and_self_loop(tmp_path):
    client = cli.FixtureClient(FIXTURE)
    client.data["relations"][A] = {"references": [{"citedPaper": {"paperId": A}}], "citations": []}
    path = tmp_path / "seed-only.db"
    with PaperStore(path) as store:
        run = collect(client, store, SEEDS[:1])["run_id"]
    result = discovery.discover(path, run, include_seeds=True)
    assert result["candidates"] == [] and result["statistics"]["candidate_count"] == 0
    seed = result["seeds"][0]
    assert seed["local_in_degree"] == seed["local_out_degree"] == 1
    assert seed["referenced_by_seed_count"] == seed["cites_seed_count"] == 0


def test_latest_known_metadata_changes_not_membership(saved):
    path, run = saved
    original = discovery.discover(path, run)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE papers SET year = 2030, citation_count = 999 WHERE paper_id = ?", (F,))
    updated = discovery.discover(path, run, year_min=2030)["candidates"]
    assert ids(updated) == [F] and updated[0]["global_citation_count"] == 999
    old = next(row for row in original["candidates"] if row["paperId"] == F)
    assert old["year"] is None and updated[0]["evidence"] == old["evidence"]


@pytest.mark.parametrize("options", [
    {"year_min": 2025, "year_max": 2020}, {"citation_min": -1}, {"year_min": True},
    {"citation_max": 1.5}, {"keywords": "graph"}, {"keywords": [""]}, {"categories": "shared_reference"},
    {"categories": ["unknown"]}, {"keywords": None}, {"categories": None},
    {"include_seeds": 1}, {"sort_by": "pagerank"}, {"top": 0}, {"top": 101},
])
def test_bad_library_options_rejected_before_db_access(monkeypatch, options):
    monkeypatch.setattr(discovery, "load_graph", Mock(side_effect=AssertionError("No DB access")))
    with pytest.raises(ValueError):
        discovery.discover("unused.db", 1, **options)


@pytest.mark.parametrize("args", [
    ["--year-min", "2025", "--year-max", "2020"], ["--citation-max", "-1"],
    ["--keyword", " "], ["--top", "0"], ["--top", "101"], ["--run-id", "0"],
    ["--run-id", str(2**64)], ["--sort-by", "pagerank"], ["--category", "unknown"],
])
def test_invalid_cli_options_rejected_before_db_access(monkeypatch, args):
    monkeypatch.setattr(PaperStore, "open_readonly", Mock(side_effect=AssertionError("No DB access")))
    with pytest.raises(SystemExit) as error:
        discovery_cli.main(["--db", "unused.db", "--run-id", "1", *args])
    assert error.value.code == 2


def test_missing_db_is_not_created(tmp_path, capsys):
    path = tmp_path / "absent.db"
    assert discovery_cli.main(["--db", str(path), "--run-id", "1", "--json"]) == 1
    assert not path.exists() and not capsys.readouterr().out


def test_cli_text_json_filters_and_redaction(saved, capsys, monkeypatch):
    args = ["--db", str(saved[0]), "--run-id", str(saved[1]), "--keyword", "graph", "--sort-by", "year", "--top", "2"]
    assert discovery_cli.main(args) == 0
    output = capsys.readouterr().out
    assert "Current collected sample" in output and "latest_known" in output
    assert "Candidates (showing 2 of 3" in output and "tie_count=" in output
    assert "Synthetic offline fixture" in output and "no research-value meaning" in output
    assert "Seeds (" not in output
    assert discovery_cli.main(args + ["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == discovery.discover(*saved, keywords=["graph"], sort_by="year", top=2)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "Graph")
    assert discovery_cli.main(args + ["--json"]) == 0
    assert "Graph" not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        discovery_cli.main(["--run-id", "Graph"])
    assert "Graph" not in capsys.readouterr().err


@pytest.mark.parametrize("encoding", [None, "cp936", "utf-8"])
@pytest.mark.parametrize("as_json", [False, True])
def test_script_unicode_output(saved, encoding, as_json):
    path, run = saved
    title = "中文论文 ő 🧪"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE papers SET title = ? WHERE paper_id = ?", (title, C))
    env = dict(os.environ)
    env.pop("SEMANTIC_SCHOLAR_API_KEY", None)
    env.pop("PYTHONIOENCODING", None)
    if encoding:
        env["PYTHONIOENCODING"] = encoding
    args = [sys.executable, str(ROOT / "scripts/discover.py"), "--db", str(path), "--run-id", str(run), "--top", "1"]
    if as_json:
        args.append("--json")
    process = subprocess.run(args, capture_output=True, env=env, timeout=20, cwd=ROOT)
    assert process.returncode == 0, process.stderr
    if as_json:
        assert json.loads(process.stdout)["candidates"][0]["title"] == title
    elif encoding:
        assert title.encode(encoding, errors="backslashreplace") in process.stdout
    else:
        assert b"Candidates (showing 1" in process.stdout
