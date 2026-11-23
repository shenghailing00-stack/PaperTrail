"""Offline UI acceptance. AppTest is a widget simulator, not a real browser."""

import csv
import io
from pathlib import Path
import re
from unittest.mock import Mock
from urllib.parse import unquote

import pytest
import requests
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.errors import AppTestError

from papertrail import api, ui_service as service, ui, library
from scripts.benchmark_ui_graph import synthetic_graph
from papertrail.cli import FixtureClient
from papertrail.collector import collect
from papertrail.store import PaperStore, SourceMismatch


A, B, C, D = (letter * 40 for letter in "abcd")
ROOT = Path(service.__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("No real HTTP")))


@pytest.fixture
def view(tmp_path):
    return service.load_view(*service.open_demo(tmp_path))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    return AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30).run()


def widget(elements, suffix):
    return next(element for element in elements if element.key and element.key.endswith(suffix))


def operation_button(app, name):
    # Fingerprint + attempt keys intentionally retire widgets from old submissions.
    return next(button for button in app.button if button.key and button.key.startswith(name + "_"))


def assert_duplicate_is_blocked(app, name, action):
    button = operation_button(app, name)
    with pytest.raises(AppTestError, match="disabled"):
        button.click()
    # AppTest cannot inject a disabled widget event. Exercise the same server
    # guard with the current session ticket, independently of frontend disabling.
    fingerprint = button.key.split("_")[-2]
    kind = "query" if name == "query_seeds" else "collection"
    operation = app.session_state["operations"][(kind, fingerprint)]
    assert not service.execute_operation(operation, operation["attempt"], action)


@pytest.mark.parametrize("text, expected", [
    ("10.1234/abc", [("id", "DOI:10.1234/abc")]),
    ("https://doi.org/10.1234/ABC", [("id", "DOI:10.1234/abc")]),
    ("arxiv:1706.03762", [("id", "ARXIV:1706.03762")]),
    ("1706.03762v2", [("id", "ARXIV:1706.03762v2")]),
    ("hep-th/9901001", [("id", "ARXIV:hep-th/9901001")]),
    ("https://arxiv.org/abs/1706.03762", [("id", "ARXIV:1706.03762")]),
    ("A paper title\n1706.03762\n10.1234/abc", [("title", "A paper title"), ("id", "ARXIV:1706.03762"), ("id", "DOI:10.1234/abc")]),
])
def test_seed_input_normalization(text, expected):
    assert service.parse_inputs(text) == expected


@pytest.mark.parametrize("text", ["", " \n ", "a\nb\nc\nd", "x"*501, "title\x00bad", "DOI:"])
def test_invalid_inputs(text):
    with pytest.raises(ValueError):
        service.parse_inputs(text)


@pytest.mark.parametrize("budget, requests_budget", [(-1, 10), (1001, 10), (True, 10), (1.5, 10), (150, 0), (150, 301)])
def test_invalid_collection_parameters_before_client(tmp_path, monkeypatch, budget, requests_budget):
    constructor = Mock(side_effect=AssertionError("must validate first"))
    monkeypatch.setattr(service, "S2Client", constructor)
    with pytest.raises(ValueError):
        service.collect_online(tmp_path / "never.db", [A], budget, requests_budget)
    assert not constructor.called


def test_title_search_reuses_safe_http_request():
    client = api.S2Client()
    client.request_json = Mock(return_value={"status": "ok"})
    try:
        client.search_titles("  retrieval graph  ")
        client.request_json.assert_called_once_with("/paper/search", {
            "query": "retrieval graph", "limit": 5, "offset": 0, "fields": ",".join(api.PAPER_FIELDS)})
        for query, limit in [(" ", 5), ("x"*501, 5), ("ok", 11), ("ok", True)]:
            with pytest.raises(ValueError):
                client.search_titles(query, limit=limit)
    finally:
        client.session.close()


def test_title_search_budget_and_redaction(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "TEST-SECRET")
    response = Mock(status_code=200)
    response.json.return_value = {"data": [{"paperId": A, "title": "TEST-SECRET paper"}]}
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    mocked = Mock(return_value=response)
    monkeypatch.setattr(requests.Session, "get", mocked)
    rows = service.resolve_inputs([("title", "paper")])
    assert "TEST-SECRET" not in repr(rows)
    assert mocked.call_args.kwargs["timeout"] == api.TIMEOUT
    assert mocked.call_args.kwargs["allow_redirects"] is False
    client = api.S2Client()
    client.budget = api.RequestBudget(0)
    try:
        assert client.search_titles("paper")["error"]["kind"] == "request_budget"
        assert mocked.call_count == 1
    finally:
        client.session.close()


def test_query_requires_confirmation_and_stops_on_error(monkeypatch):
    client = Mock()
    client.search_titles.return_value = {"status": "ok", "response": {"data": [{"paperId": A, "title": "First"}, {"paperId": B, "title": "Second"}]}}
    monkeypatch.setattr(service, "S2Client", Mock(return_value=client))
    inputs = [("title", "title")]
    results = service.resolve_inputs(inputs)
    assert client.budget.limit == 9
    with pytest.raises(ValueError, match="明确选择"):
        service.confirm_seeds(inputs, results, [None])
    with pytest.raises(ValueError):
        service.confirm_seeds(inputs, results, [C])
    assert service.confirm_seeds(inputs, results, [B]) == [B]
    client.search_titles.return_value = {"status": "error", "error": {"message": "Rate limited"}}
    client.search_titles.reset_mock()
    with pytest.raises(ValueError, match="Rate limited"):
        service.resolve_inputs(inputs * 3)
    assert client.search_titles.call_count == 1
    assert client.session.close.called


@pytest.mark.parametrize("body", [None, [], {"data": None}, {"data": [{}]*6}])
def test_invalid_search_responses_fail_closed(body, monkeypatch):
    client = Mock()
    client.search_titles.return_value = {"status": "ok", "response": body}
    monkeypatch.setattr(service, "S2Client", Mock(return_value=client))
    with pytest.raises(ValueError, match="无效"):
        service.resolve_inputs([("title", "paper")])
    assert client.session.close.called


def test_empty_search_does_not_select_anything(monkeypatch):
    client = Mock()
    client.search_titles.return_value = {"status": "ok", "response": {"data": []}}
    monkeypatch.setattr(service, "S2Client", Mock(return_value=client))
    results = service.resolve_inputs([("title", "no matches")])
    assert results == [[]]
    with pytest.raises(ValueError):
        service.confirm_seeds([("title", "no matches")], results, [None])
    client.search_titles.return_value = {"status": "ok", "response": {"data": [{"paperId": 123}, None]}}
    assert service.resolve_inputs([("title", "bad records")]) == [[]]


def test_demo_is_offline_reuses_run_and_readonly(view, monkeypatch):
    monkeypatch.setattr(service, "S2Client", Mock(side_effect=AssertionError("No API client")))
    path, run_id = view["identity"]
    before = Path(path).read_bytes()
    assert service.open_demo(Path(path).parent) == (path, run_id)
    assert service.list_runs(path)[0]["run_id"] == run_id
    result = service.load_view(path, run_id)["result"]
    assert result["sample"]["mode"] == "offline_fixture"
    assert result["statistics"]["paper_count"] == 4
    assert Path(path).read_bytes() == before


def test_source_isolation_before_http(tmp_path, monkeypatch):
    path, _ = service.open_demo(tmp_path)
    client = api.S2Client()
    client.metadata = Mock(side_effect=AssertionError("HTTP before identity check"))
    monkeypatch.setattr(service, "S2Client", Mock(return_value=client))
    with pytest.raises(SourceMismatch):
        service.collect_online(path, [A], 150, 10)
    assert not client.metadata.called


def test_candidate_groups_filter_sort_and_counts(view):
    groups = service.candidate_groups(view)
    assert [row["paperId"] for row in groups["shared_reference"]] == [C]
    assert [row["paperId"] for row in groups["shared_citing"]] == [D]
    assert "2/2" in service.relation_text(groups["shared_citing"][0], 2)
    assert all(not group for group in service.candidate_groups(view, year_min=2020, citation_min=0).values())
    assert view["result"]["statistics"]["candidate_count"] == 2
    single = dict(groups["shared_citing"][0], cites_seed_count=1)
    assert "共同" not in service.relation_text(single, 2)
    # Known counts/years in a copied presentation row exercise actual filtering/ranking.
    view["rows"][C].update(year=2020, global_citation_count=4)
    view["rows"][D].update(year=2021, global_citation_count=8)
    groups = service.candidate_groups(view, year_min=2021, year_max=2025,
                                      citation_min=5, citation_max=10, keywords=["synthetic"], sort_by="year")
    assert groups["shared_reference"] == []
    assert groups["shared_citing"][0]["sort_value"] == 2021


def test_graph_selection_identity_and_display_limit(view):
    before = set(view["graph"].edges)
    figure, visible = service.network_figure(view["graph"], 3)
    assert len(visible) == 3 and {A, B} <= set(visible)
    assert set(view["graph"].edges) == before
    assert len(figure.layout.annotations) == view["graph"].subgraph(visible).number_of_edges()
    assert figure.data[0].marker.symbol != figure.data[1].marker.symbol
    event = {"selection": {"points": [{"customdata": [C]}]}}
    assert service.selected_node(event, visible) == C
    assert service.selected_node({"selection": {"points": [{"customdata": ["foreign"]}]}}, visible) is None
    assert view["rows"][C]["abstract"] == "Example abstract."


def test_exports_selection_notes_demo_provenance_and_escaping(view):
    view["rows"][C]["title"] = '=SUM(1,2) | 中文\nline'
    csv_bytes, markdown = service.export_reading_list(view, [C, D, C], {C: "想复现 | 方法\n再读", D: "+reason"})
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    assert len(rows) == 2
    assert rows[0]["论文标题"].startswith("'=SUM")
    assert rows[0]["年份"] == "" and rows[0]["来源链接"] == ""
    assert rows[0]["阅读理由"] == "想复现 | 方法\n再读"
    assert rows[1]["阅读理由"] == "'+reason"
    assert "演示数据" in rows[0]["数据来源"]
    assert rows[0]["采集范围"] == "complete"
    assert "2/2" in rows[0]["入选依据"]
    assert "\\|" in markdown.decode() and "<br>" in markdown.decode()
    with pytest.raises(KeyError):
        service.export_reading_list(view, ["another-run-id"], {})


def test_read_snapshot_and_exports_redact_configured_secret(view, monkeypatch):
    path, run_id = view["identity"]
    secret = "configured-test-secret"
    with PaperStore(path) as store, store.connection:
        store.connection.execute("UPDATE papers SET title=?, abstract=? WHERE paper_id=?", (secret, secret, C))
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", secret)
    reopened = service.load_view(path, run_id)
    assert secret not in repr(reopened["result"])
    assert reopened["graph"].nodes[C]["title"] == "[REDACTED]"
    csv_data, markdown = service.export_reading_list(reopened, [C], {C: secret})
    assert secret.encode() not in csv_data + markdown


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        service.list_runs(path)
    with pytest.raises(ValueError):
        service.load_view(path, 1)
    assert not path.exists()


def test_real_links_and_safe_schemes():
    assert service.source_link({"externalIds": {"DOI": "10.1234/test"}}) == "https://doi.org/10.1234/test"
    assert service.source_link({"externalIds": {"ArXiv": "1706.03762"}}).endswith("/1706.03762")
    assert service.source_link({"paperId": A, "url": "javascript:alert(1)"}).startswith("https://www.semanticscholar.org/")
    assert service.source_link({"paperId": A}, demo=True) == ""


def test_demo_widgets_select_node_reading_and_rerun(app, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("render must never collect"))
    monkeypatch.setattr(service, "collect_online", forbidden)
    monkeypatch.setattr(service, "resolve_inputs", forbidden)
    app.button(key="open_demo").click().run()
    assert not app.exception
    assert [item.value for item in app.metric] == ["4", "2", "2"]
    assert any("演示数据" in item.value for item in app.markdown)
    widget(app.selectbox, "_node").select(C).run()
    assert any(item.value == "Example abstract." for item in app.text)
    widget(app.button, "add_node").click().run()
    edit_library(app, {0: {"备注": "先读方法", "状态": "在读"}})
    widget(app.slider, "_limit").set_value(3).run()
    saved = library.items(library.path_for(app.session_state["view"]))
    assert [r["paper_id"] for r in saved] == [C]
    assert saved[0]["note"] == "先读方法" and saved[0]["status"] == "reading"
    assert any("✓ 已在阅读库 · 在读" == item.value for item in app.text)
    assert not forbidden.called and not app.exception
    assert len(app.get("download_button")) == 2


def test_filters_pass_integers_and_group_sorts(app, monkeypatch):
    app.button(key="open_demo").click().run()
    actual = service.candidate_groups
    spy = Mock(wraps=actual)
    monkeypatch.setattr(service, "candidate_groups", spy)
    widget(app.checkbox, "use_year").check().run()
    widget(app.number_input, "year_min").set_value(2020).run()
    widget(app.number_input, "year_max").set_value(2025).run()
    widget(app.checkbox, "use_count").check().run()
    widget(app.text_input, "keywords").input("synthetic, example").run()
    widget(app.selectbox, "_sort").select("年份").run()
    options = spy.call_args.kwargs
    assert type(options["year_min"]) is int and options["year_min"] == 2020
    assert options["year_max"] == 2025 and type(options["citation_min"]) is int
    assert options["keywords"] == ["synthetic", "example"] and options["sort_by"] == "year"
    assert not app.exception


def test_new_collection_explicit_confirmation_and_no_duplicate_calls(app, monkeypatch):
    queried = Mock(return_value=[[{"paperId": A, "title": "First", "year": 2020, "authors": [{"name": "Author A"}]},
                                 {"paperId": B, "title": "Second", "year": 2021}]])
    monkeypatch.setattr(service, "resolve_inputs", queried)
    # Real collector, strictly offline transport, writing canonical metadata for this test.
    def fixture_collection(path, seeds, budget, request_budget):
        client = FixtureClient(service.FIXTURE)
        for paper in list(client.data["metadata"].values()):
            client.data["metadata"][paper["paperId"]] = paper
        with PaperStore(path) as store:
            return collect(client, store, seeds, citation_budget=budget, request_budget=request_budget)
    collection = Mock(side_effect=fixture_collection)
    monkeypatch.setattr(service, "collect_online", collection)
    app.segmented_control(key="entry_mode").set_value("新建采集").run()
    app.text_area(key="seed_text").input("title").run()
    assert not queried.called and operation_button(app, "collect").disabled
    app.checkbox(key="authorized").check().run()
    operation_button(app, "query_seeds").click().run()
    assert not app.exception
    assert app.selectbox(key="query_1_0").value is None
    app.button(key="confirm_seeds").click().run()
    assert app.error and operation_button(app, "collect").disabled
    app.selectbox(key="query_1_0").select(B).run()
    app.button(key="confirm_seeds").click().run()
    assert not operation_button(app, "collect").disabled
    app.text_input(key="collect_path").input(str(service.ROOT / "collected.db")).run()
    operation_button(app, "collect").click().run()
    assert not app.exception and collection.call_count == 1
    assert collection.call_args.args[1] == [B]
    widget(app.slider, "_limit").set_value(5).run()
    app.run()
    assert queried.call_count == 1 and collection.call_count == 1
    app.selectbox(key="query_1_0").select(A).run()
    assert operation_button(app, "collect").disabled
    app.text_area(key="seed_text").input("changed title").run()
    assert operation_button(app, "collect").disabled


@pytest.mark.parametrize("request_budget, expected", [(100, "bounded"), (1, "incomplete")])
def test_bounded_and_failed_sample_ui(app, tmp_path, request_budget, expected):
    path = tmp_path / "status.db"
    with PaperStore(path) as store:
        report = collect(FixtureClient(service.FIXTURE), store, service.DEMO_SEEDS[:1],
                         citation_budget=1, page_size=2, request_budget=request_budget)
    assert report["status"] == expected
    app.button(key="open_demo").click().run()
    previous_identity = app.session_state["view"]["identity"]
    app.text_input(key="open_path").input(str(path)).run()
    app.button(key="FormSubmitter:open_run-按编号打开").click().run()
    assert not app.exception
    if expected == "bounded":
        assert any("按预算采样" in item.value for item in app.text)
        assert any("部分样本" in item.value for item in app.text)
        assert any("达到预算" in item.value for item in app.text)
    else:
        assert any("采集未完成" in error.value for error in app.error)
        assert app.session_state["view"]["identity"] == previous_identity
        assert [item.value for item in app.metric] == ["4", "2", "2"]


def test_reading_library_shared_across_databases_with_source_filter(app, tmp_path):
    app.button(key="open_demo").click().run()
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node").click().run()
    edit_library(app, {0: {"备注": "跨样本保留"}})
    first_identity = app.session_state["view"]["identity"]
    other_path, run_id = service.open_demo(tmp_path / "other")
    assert run_id == first_identity[1]
    app.text_input(key="open_path").input(other_path).run()
    app.button(key="FormSubmitter:open_run-按编号打开").click().run()
    widget(app.checkbox, "_current").check().run()
    assert not any("editor_" in f.proto.id for f in app.dataframe)
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node_source").click().run()
    rows = library.items(library.path_for(app.session_state["view"]))
    assert len(rows) == 1 and len(rows[0]["sources"]) == 2
    assert rows[0]["note"] == "跨样本保留"
    assert any("editor_" in f.proto.id for f in app.dataframe)
    assert not app.exception


@pytest.mark.parametrize("size", [100, 200])
def test_network_annotations_are_batched_and_preserve_direction(size, monkeypatch):
    graph = synthetic_graph(size)
    graph.add_edge(list(graph)[0], list(graph)[0])  # Self-loop path must also batch.
    monkeypatch.setattr(service.go.Figure, "add_annotation", Mock(side_effect=AssertionError("Never append one arrow at a time")))
    original = service.go.Figure.update_layout
    batches = []
    def track(figure, *args, **kwargs):
        if "annotations" in kwargs:
            batches.append(kwargs["annotations"])
        return original(figure, *args, **kwargs)
    monkeypatch.setattr(service.go.Figure, "update_layout", track)
    figure, nodes = service.network_figure(graph, size)
    assert len(batches) == 1 and len(batches[0]) == graph.number_of_edges()
    assert len(figure.layout.shapes) == 1
    positions = service.nx.spring_layout(graph.subgraph(nodes), seed=42)
    for (a, b), arrow in zip(graph.subgraph(nodes).edges, figure.layout.annotations):
        if a != b:
            assert arrow.x == pytest.approx(positions[a][0] + .88 * (positions[b][0] - positions[a][0]))
            assert arrow.ax == pytest.approx(positions[a][0] + .12 * (positions[b][0] - positions[a][0]))
            assert arrow.y == pytest.approx(positions[a][1] + .88 * (positions[b][1] - positions[a][1]))
            assert arrow.arrowhead == 2
            assert arrow.standoff == (14 if graph.nodes[b]["is_seed"] else 0)
    assert figure.data[0].marker.color == "#8E8E93" and figure.data[1].marker.color == "#007AFF"
    assert figure.data[-1].marker.symbol == "diamond"
    assert figure.data[-1].marker.size == 24
    assert figure.data[-1].marker.line.color == "#FFFFFF"
    assert figure.data[-1].marker.line.width == 2
    drawn = [item[0] for trace in figure.data for item in trace.customdata]
    assert len(drawn) == len(nodes) and set(drawn) == set(nodes)
    assert all(graph.nodes[item[0]]["is_seed"] for item in figure.data[-1].customdata)
    assert figure.layout.clickmode == "event+select"


def test_graph_cache_is_per_opened_view_and_limit(view, monkeypatch):
    layout = Mock(wraps=service.nx.spring_layout)
    monkeypatch.setattr(service.nx, "spring_layout", layout)
    first = service.cached_network_figure(view, 100)
    assert service.cached_network_figure(view, 100) is first
    assert layout.call_count == 1
    limited = service.cached_network_figure(view, 3)
    assert len(limited[1]) == 3 and layout.call_count == 2
    assert len(view["graph"]) == 4
    reopened = service.load_view(*view["identity"])
    service.cached_network_figure(reopened, 3)
    assert layout.call_count == 3
    view["identity"] = (view["identity"][0], view["identity"][1] + 1)
    service.cached_network_figure(view, 3)
    assert layout.call_count == 4


def test_unrelated_ui_changes_reuse_drawing(app, monkeypatch):
    builder = Mock(wraps=service.network_figure)
    monkeypatch.setattr(service, "network_figure", builder)
    app.button(key="open_demo").click().run()
    assert builder.call_count == 1
    widget(app.text_input, "statistics_keywords").input("synthetic").run()
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node").click().run()
    edit_library(app, {0: {"备注": "notes"}})
    widget(app.checkbox, "use_year").check().run()
    assert builder.call_count == 1
    widget(app.slider, "_limit").set_value(3).run()
    assert builder.call_count == 2 and not app.exception


def test_keyword_statistics_full_sample_union_and_missing_fields(view):
    view["rows"][A]["abstract"] = "synthetic synthetic"
    view["rows"][B].pop("abstract", None)
    view["rows"][C].pop("title", None)
    assert service.keyword_statistics(view, "Synthetic, synthetic，Example") == [
        {"keyword": "synthetic", "title_paper_count": 3, "abstract_paper_count": 1, "either_paper_count": 3},
        {"keyword": "example", "title_paper_count": 0, "abstract_paper_count": 1, "either_paper_count": 1}]
    assert service.keyword_statistics(view, " , ， ") == []


def test_keyword_statistics_rows_match_full_one_hop_sample(view, monkeypatch):
    # In the current one-hop model, seeds plus candidates cover the full run.
    # Compare against discovery's full-sample statistics, not a fixed fixture size.
    spy = Mock(wraps=service.sample_statistics)
    monkeypatch.setattr(service, "sample_statistics", spy)

    service.keyword_statistics(view, "synthetic, example")

    spy.assert_called_once()
    rows = list(spy.call_args.args[0])
    assert len(rows) == view["result"]["statistics"]["paper_count"]
    assert len(rows) == len({row["paperId"] for row in rows})
    assert {row["paperId"] for row in rows} == set(view["graph"])


def test_keyword_ui_uses_full_run_despite_filters_and_node_cap(app, monkeypatch):
    spy = Mock(wraps=service.sample_statistics)
    monkeypatch.setattr(service, "sample_statistics", spy)
    app.button(key="open_demo").click().run()
    widget(app.checkbox, "use_year").check().run()  # No non-seed candidate has a year.
    widget(app.slider, "_limit").set_value(3).run()
    widget(app.text_input, "statistics_keywords").input("synthetic, example").run()
    frame = next(element.value for element in app.dataframe if "标题出现论文数" in element.value.columns)
    assert frame.to_dict("records") == [
        {"关键词": "synthetic", "标题出现论文数": 4, "摘要出现论文数": 0, "标题或摘要出现论文数": 4},
        {"关键词": "example", "标题出现论文数": 0, "摘要出现论文数": 1, "标题或摘要出现论文数": 1}]
    assert len(list(spy.call_args.args[0])) == 4
    assert any("不代表该领域的全部文献" in item.value for item in app.text)
    assert not app.exception


def test_operation_reentrant_duplicate_and_stale_attempts():
    session = {}
    op = service.operation_state(session, "query", "original input")
    nested = Mock()
    def action():
        assert op["status"] == "running"
        assert not service.execute_operation(op, 0, nested)
        return ["result"]
    run = Mock(side_effect=action)
    assert service.execute_operation(op, 0, run)
    assert not service.execute_operation(op, 0, run)
    assert op["status"] == "succeeded" and op["result"] == ["result"]
    assert run.call_count == 1 and not nested.called
    assert service.prepare_retry(op, 0)
    assert not service.prepare_retry(op, 0)  # Queued retry click from the old UI.
    assert not service.execute_operation(op, 0, run)
    assert service.execute_operation(op, 1, lambda: ["second"])
    assert op["result"] == ["second"]
    other = service.operation_state(session, "query", "changed input")
    assert other["status"] == "ready"
    assert service.operation_state(session, "query", "original input") is op
    assert op["status"] == "succeeded"  # Changing away and back does not re-arm.


@pytest.mark.parametrize("status", ["incomplete", "interrupted", "error"])
def test_failed_collection_operations_cannot_become_success_by_rerun(status):
    op = service.operation_state({}, "collection", ("db", (A,), 150, 100))
    run = Mock(return_value={"status": status})
    predicate = lambda report: report["status"] in {"ok", "bounded"}
    assert service.execute_operation(op, 0, run, successful=predicate)
    assert op["status"] == "failed" and op["result"]["status"] == status
    assert not service.execute_operation(op, 0, run, successful=predicate)
    assert run.call_count == 1
    assert service.prepare_retry(op, 0)
    assert service.execute_operation(op, 1, lambda: {"status": "bounded"}, successful=predicate)
    assert op["status"] == "succeeded"


def test_operation_errors_and_interruptions_stay_failed(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-secret")
    op = service.operation_state({}, "query", "title")
    error = Mock(side_effect=ValueError("bad test-secret input"))
    service.execute_operation(op, 0, error)
    assert op["status"] == "failed" and op["error"] == "bad [REDACTED] input"
    service.execute_operation(op, 0, error)
    assert error.call_count == 1
    service.prepare_retry(op, 0)
    class Interrupted(BaseException):
        pass
    with pytest.raises(Interrupted):
        service.execute_operation(op, 1, Mock(side_effect=Interrupted()))
    assert op["status"] == "failed" and op["result"] is None
    op["status"] = "running"
    assert not service.prepare_retry(op, 1)


def configure_query_app(app, monkeypatch, *, response=None):
    query = Mock(return_value=response or [[{"paperId": A, "title": "Pre-training", "authors": [{"name": None}]}]])
    monkeypatch.setattr(service, "resolve_inputs", query)
    app.segmented_control(key="entry_mode").set_value("新建采集").run()
    app.text_area(key="seed_text").input("Pre-training").run()
    app.checkbox(key="authorized").check().run()
    return query


def test_query_ui_duplicate_events_and_explicit_retry(app, monkeypatch):
    query = configure_query_app(app, monkeypatch)
    old_key = operation_button(app, "query_seeds").key
    operation_button(app, "query_seeds").click().run()
    assert query.call_count == 1 and operation_button(app, "query_seeds").disabled
    assert_duplicate_is_blocked(app, "query_seeds", query)
    app.run()
    assert query.call_count == 1
    assert app.selectbox(key="query_1_0").value is None
    app.selectbox(key="query_1_0").select(A).run()
    app.button(key="confirm_seeds").click().run()
    operation_button(app, "retry_query_seeds").click().run()
    assert query.call_count == 1 and operation_button(app, "query_seeds").key != old_key
    assert operation_button(app, "collect").disabled
    operation_button(app, "query_seeds").click().run()
    assert query.call_count == 2 and app.selectbox(key="query_2_0").value is None
    app.text_area(key="seed_text").input("other title").run()
    assert not operation_button(app, "query_seeds").disabled
    app.text_area(key="seed_text").input("Pre-training").run()
    assert operation_button(app, "query_seeds").disabled and query.call_count == 2
    assert not app.exception


def test_query_failure_persists_until_explicit_retry(app, monkeypatch):
    query = configure_query_app(app, monkeypatch)
    query.side_effect = ValueError("mock rate limit")
    operation_button(app, "query_seeds").click().run()
    app.run()
    assert_duplicate_is_blocked(app, "query_seeds", query)
    assert query.call_count == 1 and any("mock rate limit" in error.value for error in app.error)
    assert operation_button(app, "collect").disabled
    query.side_effect = None
    operation_button(app, "retry_query_seeds").click().run()
    operation_button(app, "query_seeds").click().run()
    assert query.call_count == 2 and not app.exception


def test_collection_ui_protects_completed_parameters_and_failure(app, monkeypatch):
    configure_query_app(app, monkeypatch)
    report = {"status": "incomplete", "run_id": 17, "request_attempts": 1}
    collect_mock = Mock(return_value=report)
    monkeypatch.setattr(service, "collect_online", collect_mock)
    operation_button(app, "query_seeds").click().run()
    app.selectbox(key="query_1_0").select(A).run()
    app.button(key="confirm_seeds").click().run()
    key = operation_button(app, "collect").key
    operation_button(app, "collect").click().run()
    assert_duplicate_is_blocked(app, "collect", collect_mock)
    app.run()
    assert collect_mock.call_count == 1 and operation_button(app, "collect").disabled
    assert any("采集未完成" in error.value for error in app.error)
    operation_button(app, "retry_collect").click().run()
    assert collect_mock.call_count == 1 and operation_button(app, "collect").key != key
    operation_button(app, "collect").click().run()
    assert collect_mock.call_count == 2
    app.number_input(key="citation_budget").set_value(160).run()
    assert not operation_button(app, "collect").disabled
    app.number_input(key="citation_budget").set_value(150).run()
    assert operation_button(app, "collect").disabled
    app.text_input(key="collect_path").input("another.db").run()
    assert not operation_button(app, "collect").disabled
    app.text_input(key="collect_path").input("papertrail.db").run()
    assert operation_button(app, "collect").disabled
    assert collect_mock.call_count == 2 and not app.exception


def test_successful_collection_ui_requires_explicit_recollection(app, view, monkeypatch):
    configure_query_app(app, monkeypatch)
    monkeypatch.setattr(service, "load_view", Mock(return_value=view))
    collection = Mock(return_value={"status": "ok", "run_id": view["identity"][1], "request_attempts": 0})
    monkeypatch.setattr(service, "collect_online", collection)
    operation_button(app, "query_seeds").click().run()
    app.selectbox(key="query_1_0").select(A).run()
    app.button(key="confirm_seeds").click().run()
    operation_button(app, "collect").click().run()
    assert_duplicate_is_blocked(app, "collect", collection)
    assert collection.call_count == 1
    widget(app.text_input, "statistics_keywords").input("synthetic").run()
    assert collection.call_count == 1
    operation_button(app, "retry_collect").click().run()
    assert collection.call_count == 1
    operation_button(app, "collect").click().run()
    assert collection.call_count == 2 and not app.exception


@pytest.mark.parametrize("title, expected", [("Pre-training Models", "Pre training Models"),
                                           ("Self–supervised  Fine-tuning", "Self supervised Fine tuning"),
                                           ("中文-标题", "中文 标题")])
def test_hyphens_normalized_only_in_title_request(title, expected):
    client = api.S2Client()
    client.request_json = Mock(return_value={"status": "ok"})
    try:
        parsed = service.parse_inputs(title)
        client.search_titles(parsed[0][1])
        assert parsed == [("title", title)]
        assert client.request_json.call_args.args[1]["query"] == expected
        for identifier in ["DOI:10.1234/pre-training", "ARXIV:hep-th/9901001"]:
            client.metadata(identifier)
            assert identifier in unquote(client.request_json.call_args.args[0])
        with pytest.raises(ValueError):
            client.search_titles("---")
    finally:
        client.session.close()


@pytest.mark.parametrize("external_ids", [None, [], ["DOI", "bad"], "bad", 17, {"DOI": [], "ArXiv": None}])
def test_optional_metadata_boundaries(view, external_ids):
    row = view["rows"][A]
    row.update(authors=[{"name": None}, {}, None, "bad", {"name": "张三"}, {"name": 123}], externalIds=external_ids)
    row.pop("title", None)
    row.pop("abstract", None)
    assert "张三" in ui.paper_label(row) and "标题缺失" in ui.paper_label(row)
    assert service.source_link(row) == "https://www.semanticscholar.org/paper/" + A
    assert service.table_rows([row], 2, False)[0]["标题"] == "标题缺失"
    assert "标题缺失" in service.export_reading_list(view, [A], {})[0].decode("utf-8-sig")
    assert service.author_names({"authors": {"name": "not a list"}}) == "作者未知"
    assert service.author_names({"authors": [{"name": None}, {}]}) == "作者未知"


def test_bad_metadata_ui_and_literal_library_titles(app, view, monkeypatch):
    row = view["rows"][A]
    row.update(authors=[{"name": None}, {}], externalIds="bad")
    row.pop("title", None)
    row.pop("abstract", None)
    payload = '![外部图片](https://bad.invalid/img) [点击](javascript:alert(1))'
    view["rows"][B]["title"] = payload
    monkeypatch.setattr(service, "load_view", Mock(return_value=view))
    app.button(key="open_demo").click().run()
    assert any(element.value == "标题缺失" for element in app.subheader)
    assert any("作者未知" in element.value for element in app.markdown)
    widget(app.selectbox, "_node").select(B).run()
    widget(app.button, "add_node").click().run()
    edit_library(app, {0: {"查看": True}})
    assert any(element.value == payload for element in app.text)
    assert all(payload not in element.label for element in app.text_input)
    assert not app.exception


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,bad", "file:///secret", "//host/path",
                                 "https://[bad", "https://host:bad/", "https://host/\nimg", "https://user:pw@host/",
                                 'https://host/\"bad', "https://host/\\bad"])
def test_source_link_rejects_malformed_and_unsafe_urls(url):
    assert not service.valid_source_url(url)
    assert service.source_link({"paperId": A, "url": url}) == "https://www.semanticscholar.org/paper/" + A


def test_markdown_untrusted_cells_are_literal_and_source_link_is_explicit(view):
    payload = '中文 "引号" ![pixel](https://bad.invalid/img) [run](javascript:alert(1)) <img src="https://bad.invalid/x"> https://bad.invalid/ ``tick` \\| cell\n下一行'
    view["rows"][C]["title"] = payload
    view["rows"][C]["url"] = "https://example.org/paper_(one)?q=中文"
    view["result"]["sample"]["mode"] = "api"
    csv_bytes, markdown = service.export_reading_list(view, [C], {C: payload})
    assert list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))[0]["论文标题"] == payload
    content = markdown.decode()
    assert "<br>" in content and "\\|" in content and "中文" in content and '"引号"' in content
    # Check the generated data row's code spans, independently locating their
    # matching fences. No external syntax may remain outside literal spans.
    row = content.splitlines()[-1]
    outside = re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", "", row)
    assert "![" not in outside and "<img" not in outside and "javascript:" not in outside
    assert "bad.invalid" not in outside
    assert "[查看来源](<https://example.org/paper_%28one%29?q=%E4%B8%AD%E6%96%87>)" in outside


def test_home_navigation_keeps_network_explicit(app, monkeypatch):
    network = Mock(side_effect=AssertionError("Navigation must stay offline"))
    monkeypatch.setattr(service, "resolve_inputs", network)
    monkeypatch.setattr(service, "collect_online", network)
    app.button(key="home_new").click().run()
    assert app.segmented_control(key="entry_mode").value == "新建采集"
    assert not app.checkbox(key="authorized").value
    assert operation_button(app, "query_seeds").disabled
    app.text_area(key="seed_text").input("A paper title").run()
    assert operation_button(app, "query_seeds").disabled
    app.checkbox(key="authorized").check().run()
    assert not operation_button(app, "query_seeds").disabled
    assert not network.called
    app.button(key="home_saved").click().run()
    assert app.segmented_control(key="entry_mode").value == "已有样本"
    assert not app.exception


def test_polished_sample_preserves_details_and_plot_safety(app):
    import json
    app.button(key="home_demo").click().run()
    assert not app.exception
    table = next(frame.value for frame in app.dataframe if "关联" in frame.value.columns)
    assert list(table.columns) == ["★", "标题", "年份", "关联", "被引次数", "原文链接"]
    assert not any(table["原文链接"])
    assert len(app.get("link_button")) == 0
    assert any(expander.label == "关于这个样本" for expander in app.expander)
    years = next(chart for chart in app.get("plotly_chart") if "years" in chart.proto.id)
    spec = json.loads(years.proto.spec)
    assert spec["layout"]["yaxis"]["rangemode"] == "tozero"
    assert spec["layout"]["yaxis"]["fixedrange"] is True
    assert spec["layout"]["xaxis"]["fixedrange"] is True
    assert json.loads(years.proto.config)["scrollZoom"] is False


@pytest.mark.parametrize("category, expected", [
    ("shared_reference", "1/3 篇种子"), ("shared_citing", "共同 · 2/3 篇种子")])
def test_table_relation_uses_group_counts(view, monkeypatch, category, expected):
    # One paper can belong to both groups with different counts.
    row = dict(view["rows"][C], referenced_by_seed_count=1, cites_seed_count=2)
    monkeypatch.setattr(service, "relation_text", Mock(side_effect=AssertionError("No prose parsing")))
    assert service.table_rows([row], 3, True, category=category)[0]["关联"] == expected
    assert service.table_rows([view["rows"][A]], 3, True)[0]["关联"] == "种子"


@pytest.mark.parametrize("action", ["list_runs", "FormSubmitter:open_run-按编号打开"])
def test_sidebar_missing_path_preserves_open_sample(app, tmp_path, action):
    app.button(key="open_demo").click().run()
    identity = app.session_state["view"]["identity"]
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node").click().run()
    edit_library(app, {0: {"备注": "保留这条备注"}})
    missing = tmp_path / "does-not-exist.db"
    app.text_input(key="open_path").input(str(missing)).run()
    app.button(key=action).click().run()
    assert app.error and not app.exception
    assert app.session_state["view"]["identity"] == identity
    assert [item.value for item in app.metric] == ["4", "2", "2"]
    assert widget(app.selectbox, "_node").value == C
    assert library.items(library.path_for(app.session_state["view"]))[0]["note"] == "保留这条备注"
    assert not missing.exists()


@pytest.mark.parametrize("status", ["incomplete", "running", "interrupted", "error"])
def test_load_view_rejects_saved_status_before_analysis(view, monkeypatch, status):
    path, run_id = view["identity"]
    with PaperStore(path) as store, store.connection:
        store.connection.execute("UPDATE collection_runs SET status=? WHERE run_id=?", (status, run_id))
    analysis = Mock(side_effect=AssertionError("Incomplete sample must not reach analysis"))
    monkeypatch.setattr(service, "discover", analysis)
    with pytest.raises(ValueError, match="采集未完成"):
        service.load_view(path, run_id)
    assert not analysis.called


def edit_library(app, changes):
    # AppTest has no data_editor setter. Deliver the frontend JSON widget event
    # through its runner, exercising Streamlit's actual on_change callback.
    import json
    editor = next(frame for frame in app.dataframe if "editor_" in frame.proto.id)
    states = app._tree.get_widget_states()
    for index in reversed(range(len(states.widgets))):
        if states.widgets[index].id == editor.proto.id:
            del states.widgets[index]
    state = states.widgets.add()
    state.id = editor.proto.id
    state.string_value = json.dumps({"edited_rows": changes, "added_rows": [], "deleted_rows": []})
    app._run(states)
    assert not app.exception
    return app


@pytest.mark.parametrize("key", ["home_demo", "open_demo"])
def test_demo_open_failure_is_caught(app, monkeypatch, key):
    monkeypatch.setattr(service, "open_demo", Mock(side_effect=OSError("cannot open")))
    app.button(key=key).click().run()
    assert app.error and not app.exception
    assert "view" not in app.session_state


def test_library_new_app_restores_state_and_star(app):
    app.button(key="open_demo").click().run()
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node").click().run()
    edit_library(app, {0: {"状态": "在读", "优先级": "高", "备注": "刷新后继续读"}})
    fresh = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30).run()
    fresh.button(key="open_demo").click().run()
    editor = next(frame.value for frame in fresh.dataframe if "editor_" in frame.proto.id)
    assert editor.iloc[0]["状态"] == "在读" and editor.iloc[0]["优先级"] == "高"
    assert editor.iloc[0]["备注"] == "刷新后继续读"
    candidate = next(frame.value for frame in fresh.dataframe
                     if "关联" in frame.value.columns
                     and "Shared synthetic reference" in frame.value["标题"].tolist())
    assert candidate.iloc[0]["★"] == "★"
    assert not fresh.exception


def test_home_library_without_sample(app, view):
    view["result"]["sample"]["mode"] = "api"
    library.add(library.path_for(), view, C)
    app.run()
    app.button(key="open_library").click().run()
    assert "view" not in app.session_state
    assert any("阅读库 · 1 篇" == h.value for h in app.subheader)
    assert any("editor_" in f.proto.id for f in app.dataframe)
    assert widget(app.checkbox, "_current").disabled
    assert not app.exception


def test_editor_writes_only_changed_rows_and_survives_filter(app, monkeypatch):
    app.button(key="open_demo").click().run()
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node").click().run()
    widget(app.selectbox, "_node").select(D).run()
    widget(app.button, "add_node").click().run()
    writer = Mock(wraps=library.update_many)
    monkeypatch.setattr(library, "update_many", writer)
    edit_library(app, {1: {"状态": "已读", "备注": "只改第二篇"}})
    assert writer.call_count == 1
    assert writer.call_args.args[1] == {D: {"status": "read", "note": "只改第二篇"}}
    widget(app.segmented_control, "_status").set_value("待读").run()
    edit_library(app, {0: {"优先级": "高"}})
    assert writer.call_args.args[1] == {C: {"priority": "high"}}
    app.run()
    assert writer.call_count == 2
    saved = {r["paper_id"]: r for r in library.items(library.path_for(app.session_state["view"]))}
    assert saved[C]["note"] == "" and saved[D]["note"] == "只改第二篇"
    edit_library(app, {0: {"备注": "字" * 2001}})
    assert app.error and not app.exception
    assert library.items(library.path_for(app.session_state["view"]))[0]["note"] == ""


def test_candidate_add_and_library_remove(app):
    app.button(key="open_demo").click().run()
    assert any("· #1" in item.value for item in app.markdown)
    widget(app.selectbox, "_candidate").select(C).run()
    widget(app.button, "add_candidate").click().run()
    edit_library(app, {0: {"查看": True}})
    remove = next(b for b in app.button if b.label == "确认移出" and b.key.startswith("library_"))
    remove.click().run()
    assert not library.items(library.path_for(app.session_state["view"]))
    assert any('在引用网络或候选论文中点“加入阅读库”。' == item.value for item in app.text)
    assert not app.exception


@pytest.mark.parametrize("entry", ["node", "candidate", "library"])
def test_open_remove_popover_requires_confirmation(app, monkeypatch, entry):
    app.button(key="open_demo").click().run()
    widget(app.selectbox, "_node").select(C).run()
    widget(app.button, "add_node").click().run()
    if entry == "library":
        edit_library(app, {0: {"查看": True}})
    marker = "library_" if entry == "library" else "add_" + entry + "_remove_popover_"
    popover = next(p for p in app.get("popover")
                   if p.proto.popover.label == "移出" and marker in p.proto.id)
    remover = Mock(wraps=library.remove)
    monkeypatch.setattr(library, "remove", remover)
    # Deliver the popover-open frontend event; no confirm-button event is sent.
    states = app._tree.get_widget_states()
    for index in reversed(range(len(states.widgets))):
        if states.widgets[index].id == popover.proto.id:
            del states.widgets[index]
    event = states.widgets.add()
    event.id = popover.proto.id
    event.bool_value = True
    app._run(states)
    assert not app.exception and not remover.called
    assert next(p for p in app.get("popover") if p.proto.id == popover.proto.id).proto.popover.open
    path = library.path_for(app.session_state["view"])
    assert [r["paper_id"] for r in library.items(path)] == [C]
    confirm = next(b for b in app.button if b.label == "确认移出" and
                   (b.key.startswith("library_") if entry == "library" else "add_" + entry + "_remove_confirm_" in b.key))
    confirm.click().run()
    remover.assert_called_once_with(path, C)
    assert library.items(path) == [] and not app.exception


def test_empty_priority_displays_blank_and_can_be_cleared(app):
    app.button(key="open_demo").click().run()
    widget(app.button, "add_node").click().run()
    table = next(f.value for f in app.dataframe if "editor_" in f.proto.id)
    assert table.iloc[0]["优先级"] == "" and "★" not in table.columns
    edit_library(app, {0: {"优先级": "高"}})
    edit_library(app, {0: {"优先级": ""}})
    assert library.items(library.path_for(app.session_state["view"]))[0]["priority"] is None
    app.run()
    table = next(f.value for f in app.dataframe if "editor_" in f.proto.id)
    assert table.iloc[0]["优先级"] == "" and not app.exception
