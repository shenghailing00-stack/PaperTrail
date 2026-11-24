"""UI operations and presentation helpers, independent of Streamlit widgets.

Only resolve_inputs/collect_online perform HTTP, and only explicit UI buttons
call them. Read-only snapshots live in one browser session, keyed by DB + run.
"""

import argparse
import csv
import hashlib
from html import escape
import io
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote, urlparse

import networkx as nx
import plotly.graph_objects as go

from .api import S2Client, RequestBudget, input_id, redact
from .analysis import load_graph
from .cli import FixtureClient
from .collector import collect, validate_options, DEFAULT_TTL
from .discovery import discover, filter_papers, rank_papers, sample_statistics
from .store import PaperStore, normalize_alias


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/s2_collection.json"
DEMO_SEEDS = ["ARXIV:0000.00001", "ARXIV:0000.00002"]
CATEGORY_LABELS = {"shared_reference": "被种子引用", "shared_citing": "引用种子"}
SORT_LABELS = {"referenced_by_seed_count": "被多少颗种子引用", "cites_seed_count": "引用多少颗种子",
               "global_citation_count": "来源全局引用量", "year": "年份"}


def safe(value):
    return redact(value, os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None)


def display_text(value, fallback=""):
    """Optional external metadata is text only at the presentation boundary."""
    return value if isinstance(value, str) and value.strip() else fallback


def author_names(row):
    authors = row.get("authors")
    if not isinstance(authors, (list, tuple)):
        return "作者未知"
    names = [display_text(author.get("name")) for author in authors if isinstance(author, dict)]
    return "、".join(name for name in names if name) or "作者未知"


def operation_state(session, kind, parameters):
    """Session-local submission ledger; signatures contain no raw credentials.

    Keep completed signatures when inputs change and change back. A terminal
    attempt can only be re-armed explicitly, with a new button identity.
    """
    fingerprint = hashlib.sha256(repr(parameters).encode("utf-8")).hexdigest()
    return session.setdefault("operations", {}).setdefault((kind, fingerprint), {
        "fingerprint": fingerprint, "attempt": 0, "status": "ready", "result": None, "error": None})


def execute_operation(operation, attempt, action, *, successful=lambda result: True):
    """Reject duplicate/stale events before calling action. No implicit retries."""
    if operation["attempt"] != attempt or operation["status"] != "ready":
        return False
    operation.update(status="running", result=None, error=None)
    try:
        result = action()
        operation.update(result=result, status="succeeded" if successful(result) else "failed")
    except (ValueError, OSError, sqlite3.Error) as error:
        message = str(error) if isinstance(error, ValueError) else "本地文件或数据库访问失败，请检查路径、权限和格式。"
        operation.update(status="failed", error=safe(message))
    finally:
        # Streamlit rerun/stop exceptions inherit BaseException. Do not swallow
        # them, but do not leave an interrupted operation eligible to resubmit.
        if operation["status"] == "running":
            operation.update(status="failed", error="操作被中断，结果未确认；请检查已有任务后显式重试。")
    return True


def prepare_retry(operation, attempt):
    if operation["attempt"] != attempt or operation["status"] not in {"succeeded", "failed"}:
        return False
    operation.update(attempt=attempt + 1, status="ready", result=None, error=None, opened=False)
    return True


def parse_inputs(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not 1 <= len(lines) <= 3:
        raise ValueError("请输入 1–3 篇种子，每行一篇。")
    parsed = []
    for value in lines:
        if len(value) > 500 or any(ord(char) < 32 for char in value):
            raise ValueError("每项输入须为 1–500 个可见字符。")
        value = re.sub(r"^https?://(dx\.)?doi\.org/", "DOI:", value, flags=re.I)
        value = re.sub(r"^https?://arxiv\.org/abs/", "ARXIV:", value, flags=re.I)
        if re.fullmatch(r"10\.\d{4,9}/\S+", value):
            value = "DOI:" + value
        elif re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", value):
            value = "ARXIV:" + value
        normalized = normalize_alias(value)
        is_id = bool(re.fullmatch(r"[0-9a-fA-F]{40}", value) or
                     re.match(r"^(DOI|ARXIV):", normalized))
        if is_id:
            try:
                input_id(normalized)
            except argparse.ArgumentTypeError:
                raise ValueError("DOI/arXiv 标识符格式无效，请检查前缀与编号。") from None
        parsed.append(("id" if is_id else "title", normalized if is_id else value))
    return parsed


def resolve_inputs(inputs):
    """Explicit query action: <=3 endpoints, <=9 attempts; never auto-select."""
    client = S2Client(os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None)
    client.budget = RequestBudget(9)
    results = []
    try:
        for kind, value in inputs:
            result = client.metadata(value) if kind == "id" else client.search_titles(value)
            if result["status"] != "ok":
                raise ValueError("查询失败：" + result["error"]["message"])
            body = result.get("response")
            if not isinstance(body, dict):
                raise ValueError("查询返回了无效结果，请重试。")
            rows = [body] if kind == "id" else (body or {}).get("data")
            if not isinstance(rows, list) or len(rows) > (1 if kind == "id" else 5):
                raise ValueError("查询返回了无效候选列表，请重试。")
            choices = []
            for row in rows:
                paper_id = row.get("paperId") if isinstance(row, dict) else None
                if not isinstance(paper_id, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", paper_id):
                    continue
                if kind == "id" and ":" not in value and value != row["paperId"].lower():
                    raise ValueError("返回的论文 ID 与输入不一致。")
                if row["paperId"] not in {paper["paperId"] for paper in choices}:
                    choices.append(safe(row))
            results.append(choices)
        return results
    finally:
        client.session.close()


def confirm_seeds(inputs, results, selected):
    if not 1 <= len(inputs) <= 3 or len(results) != len(inputs) or len(selected) != len(inputs):
        raise ValueError("请先查询当前输入，并逐项确认论文。")
    ids = []
    for choices, paper_id in zip(results, selected):
        if paper_id is None or paper_id not in {row["paperId"] for row in choices}:
            raise ValueError("请为每项输入明确选择一篇论文；不会自动采用第一条。")
        if paper_id not in ids:
            ids.append(paper_id)
    return ids


def collect_online(db_path, seeds, citation_budget, request_budget):
    validate_options(seeds, citation_budget, 10000, 100, DEFAULT_TTL, request_budget)
    client = S2Client(os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None)
    try:
        with PaperStore(db_path) as store:
            return safe(collect(client, store, seeds, citation_budget=citation_budget,
                                request_budget=request_budget))
    finally:
        client.session.close()


def list_runs(db_path):
    with PaperStore.open_readonly(db_path) as store:
        return safe([dict(row) for row in store.connection.execute(
            "SELECT run_id, status, source, started_at FROM collection_runs ORDER BY run_id DESC")])


def open_demo(directory):
    """Existing fixture, fingerprint-specific DB; no HTTP client or fallback."""
    client = FixtureClient(FIXTURE)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("demo-" + client.source_key.split(":")[1] + ".db")
    if path.exists():
        with PaperStore.open_readonly(path) as store:
            identity = store.connection.execute("SELECT source_key FROM dataset_identity").fetchone()
            if identity is None or identity[0] != client.source_key:
                raise ValueError("演示库来源不匹配，请使用另一个演示目录。")
            row = store.connection.execute(
                "SELECT run_id FROM collection_runs WHERE status = 'ok' ORDER BY run_id DESC LIMIT 1").fetchone()
            if row:
                return str(path.resolve()), row[0]
    with PaperStore(path, source=client.source) as store:
        report = collect(client, store, DEMO_SEEDS, page_size=2)
    if report["status"] != "ok":
        raise ValueError("演示数据未能完整打开，请检查本地 fixture 和数据库。")
    return str(path.resolve()), report["run_id"]


def load_view(db_path, run_id):
    """Snapshot loaded on explicit open, never cache DB connections or clients."""
    # Read the saved status explicitly for the UI message; analysis still
    # performs its full validation before any new view can be returned.
    with PaperStore.open_readonly(db_path) as store:
        run = store.connection.execute(
            "SELECT status FROM collection_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is not None and run["status"] not in {"ok", "bounded"}:
            raise ValueError("采集未完成，无法分析。已获取的部分会保留，可以稍后重试。")
    result = safe(discover(db_path, run_id, include_seeds=True))
    graph = load_graph(db_path, run_id)
    # The graph is for drawing only; metrics always come from discover's full run.
    rows = {row["paperId"]: row for row in result["seeds"] + result["candidates"]}
    for paper_id, attrs in graph.nodes(data=True):
        attrs.update(safe(dict(attrs)))
        # Metadata may change between the two reads; use discover's opened view
        # consistently for hover labels and details. Run topology is immutable.
        if paper_id in rows:
            attrs.update(rows[paper_id])
    graph.graph.update(safe(dict(graph.graph)))
    return {"identity": (str(Path(db_path).resolve()), run_id), "result": result,
            "graph": graph, "rows": rows}


def candidate_groups(view, **options):
    """Reuse TASK-003 filtering/ranking; rank both directions independently."""
    rows = list(view["rows"].values())
    order = options.pop("sort_by", None)
    return {category: rank_papers(filter_papers(rows, categories=[category], **options),
                                 sort_by=order or ("referenced_by_seed_count" if category == "shared_reference"
                                                   else "cites_seed_count"))
            for category in CATEGORY_LABELS}


def source_link(row, demo=False):
    if demo:
        return ""  # Never fabricate real publication links for synthetic IDs.
    ids = row.get("externalIds")
    ids = ids if isinstance(ids, dict) else {}
    doi, arxiv = display_text(ids.get("DOI")), display_text(ids.get("ArXiv"))
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        return "https://doi.org/" + quote(doi, safe="/")
    if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", arxiv):
        return "https://arxiv.org/abs/" + quote(arxiv, safe="/")
    url = row.get("url")
    if valid_source_url(url):
        return url
    paper_id = display_text(row.get("paperId"))
    return "https://www.semanticscholar.org/paper/" + paper_id if re.fullmatch(r"[0-9a-fA-F]{40}", paper_id) else ""


def valid_source_url(value):
    """Validate URL syntax/scheme locally, not the existence of a publication."""
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        return False
    try:
        parsed = urlparse(value)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password and (parsed.port is None or parsed.port > 0)
                and not any(char in value for char in '<>"\\'))
    except ValueError:
        return False


def keyword_statistics(view, text):
    terms = [term.strip() for term in text.replace("，", ",").split(",") if term.strip()]
    return sample_statistics(view["rows"].values(), keywords=terms)["keyword_counts"]


def relation_text(row, seed_count):
    if row["is_seed"]:
        return "种子论文（用户选择，独立于候选排序）"
    reasons = []
    for field, text in (("referenced_by_seed_count", "被{n}种子引用"),
                        ("cites_seed_count", "引用{n}种子")):
        count = row[field]
        if count:
            reasons.append(("共同：" if count >= 2 else "") + text.format(n=f" {count}/{seed_count} 颗"))
    return "；".join(reasons)


def table_rows(rows, seed_count, demo, *, category=None):
    count_fields = {"shared_reference": "referenced_by_seed_count", "shared_citing": "cites_seed_count"}
    displayed = []
    for row in rows:
        if row["is_seed"]:
            relation = "种子"
        else:
            count = row[count_fields[category]]
            relation = ("共同 · " if count >= 2 else "") + f"{count}/{seed_count} 篇种子"
        displayed.append({"标题": display_text(row.get("title"), "标题缺失"), "年份": row.get("year"),
                          "关联": relation, "被引次数": row["global_citation_count"],
                          "原文链接": source_link(row, demo)})
    return displayed


def network_figure(graph, max_nodes=100):
    if type(max_nodes) is not int or not 3 <= max_nodes <= 200:
        raise ValueError("显示节点上限须为 3–200 的整数。")
    nodes = sorted(graph, key=lambda p: (not graph.nodes[p]["is_seed"], p))[:max_nodes]
    display = graph.subgraph(nodes)
    pos = nx.spring_layout(display, seed=42)
    figure = go.Figure()
    annotations, shapes = [], []
    for a, b in display.edges:
        x0, y0 = map(float, pos[a])
        x1, y1 = map(float, pos[b])
        if a == b:
            shapes.append(dict(type="circle", x0=x0, x1=x0 + .12, y0=y0, y1=y0 + .12,
                               line_color="#5C5852"))
            annotations.append(dict(x=x0 + .12, y=y0 + .06, ax=x0 + .10, ay=y0 + .10,
                                    xref="x", yref="y", axref="x", ayref="y", arrowhead=2, arrowcolor="#5C5852", text=""))
        else:
            # Shorten at both ends so the arrowhead remains visible beside markers.
            annotations.append(dict(x=x0 + .88*(x1-x0), y=y0 + .88*(y1-y0),
                                  ax=x0 + .12*(x1-x0), ay=y0 + .12*(y1-y0),
                                  xref="x", yref="y", axref="x", ayref="y", text="",
                                  showarrow=True, arrowhead=2, arrowwidth=1.3, arrowcolor="#5C5852",
                                  standoff=14 if graph.nodes[b]["is_seed"] else 0))
    for is_seed, label, color, symbol in ((False, "相关论文", "#6E6A63", "circle"),
                                         (True, "种子论文", "#D4A373", "diamond")):
        group = [p for p in nodes if graph.nodes[p]["is_seed"] == is_seed]
        figure.add_trace(go.Scatter(x=[float(pos[p][0]) for p in group],
                                   y=[float(pos[p][1]) for p in group], mode="markers",
                                   name=label, customdata=[[p] for p in group],
                                   text=[escape(display_text(graph.nodes[p].get("title"), "标题缺失")) for p in group],
                                   hovertemplate="%{text}<extra>%{fullData.name}</extra>",
                                   marker=dict(size=24 if is_seed else 13, color=color, symbol=symbol,
                                               line=dict(color="#121212", width=2 if is_seed else 0)),
                                   # Plotly fades unselected points to 0.2 by default, too dark on a dark theme.
                                   unselected=dict(marker=dict(opacity=0.55))))
    figure.update_layout(annotations=annotations, shapes=shapes,
                         height=500, clickmode="event+select", dragmode="pan", margin=dict(l=20, r=20, t=20, b=20),
                         xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x"),
                         uirevision="network", legend=dict(orientation="h"))
    return figure, nodes


def cached_network_figure(view, max_nodes):
    """One cached drawing owned by the opened view, replaced on limit change.

    Reopening a run creates a new view (and refreshes metadata). No global cache,
    cross-run graph hash, or cached database/client is needed.
    """
    key = (view["identity"], max_nodes)
    cached = view.get("network_cache")
    if cached is None or cached["key"] != key:
        cached = {"key": key, "drawing": network_figure(view["graph"], max_nodes)}
        view["network_cache"] = cached
    return cached["drawing"]


def selected_node(event, allowed):
    points = (event or {}).get("selection", {}).get("points", [])
    for point in reversed(points):
        data = point.get("customdata")
        if isinstance(data, (list, tuple)) and data and data[0] in allowed:
            return data[0]
    return None


def _cell(value):
    value = str(safe(value))
    # Keep spreadsheet applications from treating external text as formulas.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value

def _md(value):
    # Literal code spans prevent images, raw HTML, links AND bare-URL
    # autolinking. Choose a fence longer than any external backtick run.
    text = str(safe(value)).replace("\r\n", "\n").replace("\r", "\n")
    parts = []
    for line in text.split("\n"):
        chunks = []
        # Escape separators outside the spans, so an external backslash
        # beside a pipe cannot change table structure or lose a character.
        for chunk in line.split("|"):
            fence = "`" * (max((len(run) for run in re.findall(r"`+", chunk)), default=0) + 1)
            chunks.append(fence + " " + chunk + " " + fence if chunk else "")
        parts.append("\\|".join(chunks))
    return "<br>".join(parts)
def _markdown_record(record):
    cells = [_md(value) for value in record]
    # Only the dedicated, locally validated source column becomes a link.
    if valid_source_url(record[2]):
        target = quote(safe(record[2]), safe="/:?=&%#+")
        cells[2] = f"[查看来源](<{target}>)"
    return "| " + " | ".join(cells) + " |"


def export_reading_list(view, selected, notes):
    result = view["result"]
    demo = result["sample"]["mode"] == "offline_fixture"
    provenance = "演示数据（合成，非真实研究成果）" if demo else result["sample"]["source"]
    headers = ["论文标题", "年份", "来源链接", "候选类别", "入选依据", "阅读理由", "数据来源", "采集范围", "run_id", "paperId"]
    records = []
    for paper_id in dict.fromkeys(selected):
        row = view["rows"][paper_id]  # Refuse stale/foreign-run selections.
        categories = "种子" if row["is_seed"] else "；".join(CATEGORY_LABELS[c] for c in row["categories"])
        records.append([display_text(row.get("title"), "标题缺失"), row.get("year") if row.get("year") is not None else "",
                        source_link(row, demo), categories,
                        relation_text(row, result["statistics"]["seed_count"]) + "；由用户手动加入",
                        notes.get(paper_id, ""), provenance, result["sample"]["collection_scope"],
                        result["sample"]["run_id"], paper_id])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows([[_cell(value) for value in record] for record in records])
    lines = ["# PaperTrail 阅读清单", "", _md(provenance),
             "仅依据当前采集样本与用户选择，不代表领域整体、科研质量或必读推荐。",
             "采集范围：" + _md(result["sample"]["collection_scope"]), ""]
    lines += ["> " + _md(warning) for warning in result["sample"]["warnings"]]
    lines += ["", "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"]*len(headers)) + " |"]
    lines += [_markdown_record(record) for record in records]
    return buffer.getvalue().encode("utf-8-sig"), "\n".join(lines).encode("utf-8")
