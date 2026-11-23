"""Single-page Streamlit UI. Network operations are inside explicit button guards."""

import hashlib
import os
import re
from pathlib import Path
import sqlite3

import streamlit as st
import plotly.graph_objects as go

from . import ui_service as service
from . import library, ui_library


STATUS_LABELS = {"ok": "完整采集", "bounded": "按预算采样", "incomplete": "未完成",
                 "running": "进行中", "error": "失败", "interrupted": "已中断"}


def literal(text):
    """Keep external titles/authors literal inside native Markdown elements."""
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", str(text))


def first_author(row):
    names = service.author_names(row).split("、")
    return names[0] + (" 等" if len(names) > 1 else "")


def paper_label(row):
    title = service.display_text(row.get("title"), "标题缺失")
    return f"{title} · {row.get('year') or '年份未知'} · {first_author(row)}"


def choose_entry(mode):
    st.session_state["entry_mode"] = mode


def open_demo():
    with st.spinner("正在打开本地演示…"):
        activate(*service.open_demo(service.ROOT / "output/ui-demo"))



def operation_button(operation, kind, label, retry_label, enabled):
    """A separate preparation click arms a new attempt, never sends HTTP itself."""
    attempt = operation["attempt"]
    key = f"{kind}_{operation['fingerprint']}_{attempt}"
    clicked = st.button(label, key=key, disabled=not enabled or operation["status"] != "ready")
    if operation["status"] != "ready":
        state = {"running": "进行中", "succeeded": "已完成", "failed": "失败"}[operation["status"]]
        st.badge(state, color="gray")
    if operation["error"]:
        st.error(operation["error"])
    if operation["status"] in {"succeeded", "failed"}:
        if st.button(retry_label, key="retry_" + key, disabled=not enabled):
            if service.prepare_retry(operation, attempt):
                if kind == "query_seeds":
                    st.session_state.pop("confirmed", None)
                    st.session_state.pop("query", None)
                st.rerun()
    return clicked and enabled and operation["status"] == "ready", attempt


def activate(db_path, run_id):
    # Replace the current sample only after the new snapshot passes validation.
    st.session_state["view"] = service.load_view(db_path, run_id)
    st.session_state["library_only"] = False


def entry_panel():
    with st.sidebar:
        st.header("开始探索")
        if st.button("试用演示", key="open_demo", type="primary"):
            open_demo()
        mode = st.segmented_control("数据入口", ["已有样本", "新建采集"], default="已有样本",
                                    required=True, key="entry_mode", label_visibility="collapsed")
        if mode == "已有样本":
            path = st.text_input("数据库路径", value="papertrail.db", key="open_path",
                                 placeholder="例如 output/my-sample.db")
            if st.button("读取任务列表", key="list_runs"):
                st.session_state.pop("run_list", None)
                st.session_state["run_list"] = (path, service.list_runs(path))
            listing = st.session_state.get("run_list")
            if listing and listing[0] == path:
                rows = {row["run_id"]: row for row in listing[1]}
                choice = st.selectbox("已保存任务", list(rows), index=None,
                                      format_func=lambda rid: f"#{rid} · {STATUS_LABELS.get(rows[rid]['status'], '未知状态')} · "
                                      f"{(rows[rid]['started_at'] or '')[5:10]}", key="saved_run")
                if not rows:
                    st.info("该数据库尚无采集任务。")
                if st.button("打开选定样本", disabled=choice is None, key="open_saved"):
                    activate(path, choice)
            with st.expander("高级"):
                with st.form("open_run"):
                    run_id = st.number_input("样本编号", min_value=1, max_value=2**53-1, value=1, step=1)
                    if st.form_submit_button("按编号打开"):
                        activate(path, int(run_id))
        else:
            collection_panel()



def collection_panel():
    text = st.text_area("论文 DOI、arXiv ID 或标题", key="seed_text", placeholder="每行一篇，最多 3 篇")
    authorized = st.checkbox("允许联网查询 Semantic Scholar", key="authorized")
    query_operation = service.operation_state(st.session_state, "query", text)
    submit, attempt = operation_button(query_operation, "query_seeds", "查询",
                                        "准备重新查询／重试", authorized)
    if submit:
        st.session_state.pop("confirmed", None)
        st.session_state.pop("query", None)
        with st.spinner("正在查询，尚未开始引用采集…"):
            service.execute_operation(query_operation, attempt,
                                      lambda: service.resolve_inputs(service.parse_inputs(text)))
        st.rerun()
    query_ticket = (query_operation["fingerprint"], query_operation["attempt"])
    if query_operation["status"] == "succeeded" and st.session_state.get("query_ticket") != query_ticket:
        st.session_state["query"] = (text, service.parse_inputs(text), query_operation["result"])
        st.session_state["query_ticket"] = query_ticket
        st.session_state["query_version"] = st.session_state.get("query_version", 0) + 1
        st.session_state.pop("confirmed", None)
    query = st.session_state.get("query")
    if query_operation["status"] == "succeeded" and query and query[0] == text:
        selected = []
        for index, choices in enumerate(query[2]):
            rows = {row["paperId"]: row for row in choices}
            if not rows:
                st.warning(f"第 {index + 1} 项没有可用结果，请修改输入后重新查询。")
            selection = st.selectbox(f"确认第 {index + 1} 篇种子", list(rows), index=None,
                                     format_func=lambda pid, rows=rows: paper_label(rows[pid]),
                                     key=f"query_{st.session_state['query_version']}_{index}")
            selected.append(selection)
            if selection:
                link = service.source_link(rows[selection])
                if link:
                    st.link_button(f"核对第 {index + 1} 篇来源", link)
        if st.button("确认这些种子", key="confirm_seeds"):
            try:
                ids = service.confirm_seeds(query[1], query[2], selected)
                st.session_state["confirmed"] = (text, tuple(selected), ids)
            except ValueError as error:
                st.session_state.pop("confirmed", None)
                st.error(str(error))
        confirmed = st.session_state.get("confirmed")
        valid = bool(confirmed and confirmed[:2] == (text, tuple(selected)))
    else:
        valid = False
    if valid:
        st.success(f"已确认 {len(confirmed[2])} 篇不同种子。")
    path = st.text_input("真实采集数据库路径", value="papertrail.db", key="collect_path")
    with st.expander("高级设置"):
        budget = st.number_input("全局被引记录预算", min_value=0, max_value=200, value=150, step=10,
                                 key="citation_budget", help="预算由种子平分，按数据源顺序采样；不是最重要的若干篇。")
        requests = st.number_input("最大请求尝试次数（含重试）", min_value=1, max_value=100, value=100, step=1,
                                   key="request_budget", help="参考文献每篇种子上限 10000，缓存有效期 7 天。")
    # Include input, confirmed results, absolute database path and all UI budgets.
    # Keep old signatures in the ledger when settings change and change back.
    parameters = (text, tuple(confirmed[1]) if valid else None, tuple(confirmed[2]) if valid else None,
                  os.path.normcase(str(Path(path).resolve())), int(budget), int(requests))
    collection_operation = service.operation_state(st.session_state, "collection", parameters)
    submit, attempt = operation_button(collection_operation, "collect", "开始采集",
                                        "准备重新采集／重试", authorized and valid)
    if submit:
        with st.spinner("正在采集一层引用；请等待任务结束…"):
            service.execute_operation(collection_operation, attempt,
                                      lambda: service.collect_online(path, confirmed[2], int(budget), int(requests)),
                                      successful=lambda report: report["status"] in {"ok", "bounded"})
        st.rerun()
    report = collection_operation["result"]
    if report:
        if report["status"] in {"ok", "bounded"} and not collection_operation.get("opened"):
            collection_operation["opened"] = True
            activate(path, report["run_id"])
        if report["status"] not in {"ok", "bounded"}:
            st.error("采集未完成，无法分析。已获取的部分会保留，可以稍后重试。")
        with st.expander("技术详情"):
            st.json(report)


def show_table(rows, seed_count, demo, *, category=None, saved_ids=()):
    if not rows:
        st.text("没有符合条件的论文。")
        return
    displayed = service.table_rows(rows, seed_count, demo, category=category)
    displayed = [{"★": "★" if row["paperId"] in saved_ids else "", **cells}
                 for row, cells in zip(rows, displayed)]
    st.dataframe(displayed, hide_index=True,
                 column_config={"★": st.column_config.TextColumn(width="small"), "原文链接": st.column_config.LinkColumn("原文链接", display_text="打开原文"),
                                "标题": st.column_config.TextColumn(width="medium"),
                                "被引次数": st.column_config.NumberColumn(format="%d", help="数据源提供的全局被引次数。"),
                                "年份": st.column_config.NumberColumn(format="%d")})


def detail(view, paper_id, prefix):
    row = view["rows"][paper_id]
    result = view["result"]
    demo = result["sample"]["mode"] == "offline_fixture"
    with st.container(border=True):
        st.subheader(literal(service.display_text(row.get("title"), "标题缺失")))
        count = row["global_citation_count"]
        meta = f"{row.get('year') or '年份未知'} · {first_author(row)} · 被引 {count:,}" if count is not None else f"{row.get('year') or '年份未知'} · {first_author(row)} · 被引次数未知"
        st.markdown(":gray[" + literal(meta) + "]")
        st.text("种子论文" if row["is_seed"] else service.relation_text(row, result["statistics"]["seed_count"]))
        abstract = service.display_text(row.get("abstract"), "摘要缺失")
        st.text(abstract[:240] + ("…" if len(abstract) > 240 else ""))
        if len(abstract) > 240:
            with st.expander("展开摘要"):
                st.text(abstract)
        original, add = st.columns(2)
        if link := service.source_link(row, demo):
            original.link_button("打开原文", link, type="primary")
        with add:
            ui_library.favorite(view, paper_id, prefix + "add_node")



def graph_panel(view, prefix):
    with st.popover("显示设置"):
        limit = st.slider("网络显示节点上限", min_value=3, max_value=200, value=100, step=1,
                          key=prefix + "limit", help="优先显示种子，其余按稳定顺序显示；此限制不改变样本、候选与统计。")
    figure, visible = service.cached_network_figure(view, limit)
    selection_key = prefix + "node"
    def on_node_selection():
        selected = service.selected_node(st.session_state.get(prefix + "network"), visible)
        if selected:
            st.session_state[selection_key] = selected
    st.plotly_chart(figure, key=prefix + "network", on_select=on_node_selection, selection_mode="points",
                    config={"displaylogo": False, "scrollZoom": False})
    st.caption("箭头 A → B：A 引用了 B")
    rows = view["rows"]
    chosen = st.selectbox("选择论文节点查看详情", list(rows), format_func=lambda pid: paper_label(rows[pid]),
                          key=selection_key)
    detail(view, chosen, prefix)
    with st.expander("引用关系列表"):
        st.dataframe([{"引用者": service.display_text(rows[a].get("title"), a),
                       "被引用者": service.display_text(rows[b].get("title"), b)}
                      for a, b in view["graph"].subgraph(visible).edges], hide_index=True)


def candidates_panel(view, prefix):
    stats = view["result"]["statistics"]
    demo = view["result"]["sample"]["mode"] == "offline_fixture"
    saved_ids = {row["paper_id"] for row in library.items(library.path_for(view))}
    st.subheader("你的种子论文")
    show_table(view["result"]["seeds"], stats["seed_count"], demo, saved_ids=saved_ids)
    options = {}
    search, filters, order = st.columns([3, 1, 2], vertical_alignment="bottom")
    keyword = search.text_input("搜索论文", placeholder="搜索标题或摘要", key=prefix + "keywords",
                                help="多个词用逗号分隔，须全部命中。", label_visibility="collapsed")
    options["keywords"] = [term.strip() for term in keyword.replace("，", ",").split(",") if term.strip()]
    with filters.popover("筛选"):
        use_year = st.checkbox("启用年份筛选", key=prefix + "use_year")
        options["year_min"] = st.number_input("最早年份", min_value=0, max_value=9999, value=1900, step=1,
                                               key=prefix + "year_min") if use_year else None
        options["year_max"] = st.number_input("最晚年份", min_value=0, max_value=9999, value=2100, step=1,
                                               key=prefix + "year_max") if use_year else None
        use_count = st.checkbox("启用被引次数筛选", key=prefix + "use_count")
        options["citation_min"] = st.number_input("最少引用量", min_value=0, value=0, step=1,
                                                   key=prefix + "citation_min") if use_count else None
        options["citation_max"] = st.number_input("最多引用量", min_value=0, value=1000000, step=1,
                                                   key=prefix + "citation_max") if use_count else None
    sort = order.selectbox("排序依据（降序）", ["分组关系数量"] + list(service.SORT_LABELS.values()),
                           key=prefix + "sort", label_visibility="collapsed",
                           help="同值并列，顺序不代表研究价值；未知排序值置后。数值筛选排除该字段缺失的论文，统计仍针对完整样本。")
    options["sort_by"] = next((key for key, value in service.SORT_LABELS.items() if value == sort), None)
    try:
        groups = service.candidate_groups(view, **options)
    except ValueError as error:
        st.error(service.safe(str(error)))
        return
    labels = {"shared_reference": ("前期工作", "种子论文引用了它们"),
              "shared_citing": ("后续研究", "它们引用了种子论文")}
    for category, rows in groups.items():
        title, explanation = labels[category]
        st.subheader(f"{title} · {len(rows)} 篇", help=explanation)
        show_table(rows, stats["seed_count"], demo, category=category, saved_ids=saved_ids)

    candidates = {row["paperId"]: row for rows in groups.values() for row in rows}
    if candidates:
        selected = st.selectbox("选择候选论文", list(candidates),
                                format_func=lambda pid: paper_label(candidates[pid]), key=prefix + "candidate")
        ui_library.favorite(view, selected, prefix + "add_candidate")


def statistics_panel(view, prefix):
    stats = view["result"]["statistics"]
    st.subheader("年份分布")
    years = stats["year_distribution"]
    if years:
        figure = go.Figure(go.Bar(x=[row["year"] for row in years],
                                  y=[row["paper_count"] for row in years], marker_color="#007AFF",
                                  hovertemplate="%{x} 年 · %{y} 篇<extra></extra>"))
        figure.update_layout(height=320, margin=dict(l=20, r=20, t=10, b=20),
                             xaxis=dict(title="年份", dtick=1, fixedrange=True),
                             yaxis=dict(title="样本论文数", rangemode="tozero", dtick=1, fixedrange=True))
        st.plotly_chart(figure, key=prefix + "years", config={"scrollZoom": False, "displayModeBar": False})
    else:
        st.text("所有论文年份未知，无法绘制年份分布。")
    with st.expander("查看数据"):
        st.dataframe(years, hide_index=True,
                     column_config={"year": st.column_config.NumberColumn("年份", format="%d"),
                                    "paper_count": "样本论文数"})
    st.subheader("关键词统计")
    keywords = st.text_input("统计关键词", placeholder="多个词用逗号分隔", key=prefix + "statistics_keywords",
                            help="统计全部样本（含种子），不受候选筛选影响。忽略大小写，按字面子串匹配，每篇计一次；标题或摘要为去重并集。")
    counts = service.keyword_statistics(view, keywords)
    if counts:
        st.dataframe([{"关键词": row["keyword"], "标题出现论文数": row["title_paper_count"],
                       "摘要出现论文数": row["abstract_paper_count"],
                       "标题或摘要出现论文数": row["either_paper_count"]} for row in counts], hide_index=True)
    st.subheader("数据完整度")
    names = {"year": "年份", "title": "标题", "abstract": "摘要", "global_citation_count": "被引次数",
             "url": "原文链接", "authors": "作者", "externalIds": "外部标识"}
    st.dataframe([{"缺失字段": names[key], "论文数": value} for key, value in stats["missing_metadata"].items()], hide_index=True)



def home_panel():
    st.caption("从几篇论文出发，找到下一篇该读的。")
    demo, saved, new = st.columns(3)
    with demo.container(border=True):
        if st.button("试用演示", key="home_demo", type="primary", width="stretch"):
            open_demo()
            st.rerun()
        st.text("用合成论文体验完整流程")
    with saved.container(border=True):
        st.button("打开已有样本", key="home_saved", on_click=choose_entry, args=("已有样本",), width="stretch")
        st.text("从左侧选择本地样本")
    with new.container(border=True):
        st.button("开始新的探索", key="home_new", on_click=choose_entry, args=("新建采集",), width="stretch")
        st.text("从一至三篇论文开始")


def sample_overview(view):
    sample, stats = view["result"]["sample"], view["result"]["statistics"]
    titles = [re.split(r"[:：]", service.display_text(row.get("title"), "标题缺失"))[0]
              for row in view["result"]["seeds"]]
    title, badge = st.columns([5, 1], vertical_alignment="center")
    title.subheader(literal(" · ".join(titles)))
    if sample["mode"] == "offline_fixture":
        badge.badge("演示数据", color="gray", help="论文与引用量均为合成内容，非真实研究成果。")
    st.markdown(":gray[" + f"{stats['paper_count']} 篇论文 · {view['graph'].number_of_edges()} 条引用关系 · "
                f"采集于 {(sample.get('started_at') or '日期未知')[:10]} · #{sample['run_id']}" + "]")
    # One shared sample caveat; tab-specific guidance stays near its controls.
    st.text("按预算采集的部分样本，不代表该领域的全部文献。")
    with st.expander("关于这个样本"):
        st.text(f"{sample['source']} · {STATUS_LABELS.get(sample['status'], '未知状态')}")
        if sample["mode"] == "offline_fixture":
            st.text("演示数据：所有论文与引用量均为合成内容，不是真实研究成果。")
        directions = {"references": "参考文献", "citations": "被引"}
        def direction_rows(items):
            return [{"论文标题": service.display_text(view["rows"].get(item["seed_id"], {}).get("title"), "标题缺失"),
                     "方向": directions.get(item["direction"], "未知方向"), "已取": item["raw_count"],
                     "上限": item["target_limit"], "数据源总数": item["expected_count"]} for item in items]
        if sample["truncated_directions"]:
            st.text("达到预算的采集方向")
            st.dataframe(direction_rows(sample["truncated_directions"]), hide_index=True)
        st.text("各方向采集覆盖")
        st.dataframe(direction_rows(sample["direction_states"]), hide_index=True)
        coverage_names = {"raw_records": "原始关系记录", "resolved_records": "可识别论文的记录",
                          "unresolved_records": "缺少论文标识的记录", "missing_abstract_records": "缺摘要的记录（含重复）",
                          "duplicate_edge_observations": "重复引用关系"}
        st.dataframe([{"数据质量": coverage_names[key], "记录数": value}
                      for key, value in sample["coverage"].items()], hide_index=True)
        st.text(f"两类候选重叠 {stats['both_category_count']} 篇，因此两类数量不能直接相加。")
        st.text("单层采样可能缺少候选之间的引用；数据源的全局被引次数与样本内观察到的引用数不同。元数据是打开样本时的最新已知值。")
        st.text("完整采集只表示到达数据源提供的末尾；按数据源顺序获取，不是最重要的若干篇，也不能据此判断论文质量、经典或基线地位。")
        st.text("统计包含种子及筛选前的全部候选，不代表领域趋势；候选只需关联一篇种子，两组可以重叠。")
        st.text("种子的样本内被引数受采样配额限制，不能与其他论文直接比较；观察到的引用关系也不是论文的完整参考文献表。")
        st.text("自引用保留为一条关系，但不计入种子关联数；分页期间数据源可能变化，采集结果不是固定时间快照。")
        st.text("种子单独展示，不参与候选排序；同值时的稳定显示顺序没有研究价值含义。")
        # Preserve every diagnostic while replacing internal identifiers for display.
        for warning in sample["warnings"]:
            for pid, row in view["rows"].items():
                warning = warning.replace(pid, service.display_text(row.get("title"), "标题缺失"))
            warning = warning.replace("expected_count", "数据源总数").replace("/references", " / 参考文献").replace("/citations", " / 被引")
            st.text(warning)
    for column, label, value in zip(st.columns(3), ["论文", "相关论文", "种子论文"],
                                    [stats["paper_count"], stats["candidate_count"], stats["seed_count"]]):
        column.metric(label, value)


def main():
    st.set_page_config(page_title="PaperTrail · 文献探索", page_icon="📚", layout="wide")
    st.title("PaperTrail")
    try:
        entry_panel()
        with st.sidebar:
            count = len(library.items(library.path_for()))
            if st.button(f"我的阅读库 · {count} 篇", key="open_library"):
                st.session_state["library_only"] = True
        if not st.session_state.get("view") and not st.session_state.get("library_only"):
            home_panel()
    except (ValueError, OSError, sqlite3.Error) as error:
        st.error("操作失败，未打开新样本：" + service.safe(str(error)) if isinstance(error, ValueError)
                 else "本地文件或数据库访问失败，请检查路径、权限和数据库格式。")
    view = st.session_state.get("view")
    if st.session_state.get("library_only"):
        if st.button("返回样本" if view else "返回首页", key="close_library"):
            st.session_state["library_only"] = False
            st.rerun()
        try:
            ui_library.panel()
        except (ValueError, OSError, sqlite3.Error):
            st.error("阅读库访问失败，请检查文件权限和数据库格式。")
        return
    if not view:
        return
    sample_overview(view)
    prefix = hashlib.sha256(repr(view["identity"]).encode()).hexdigest()[:16] + "_"
    tabs = st.tabs(["引用网络", "候选论文", "样本统计", "阅读库"])
    try:
        with tabs[0]:
            graph_panel(view, prefix)
        with tabs[1]:
            candidates_panel(view, prefix)
        with tabs[2]:
            statistics_panel(view, prefix)
        with tabs[3]:
            ui_library.panel(view)
    except (ValueError, OSError, sqlite3.Error):
        st.error("阅读库访问失败，请检查文件权限和数据库格式。")
