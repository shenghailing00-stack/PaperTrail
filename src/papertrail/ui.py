"""Single-page Streamlit UI. Network operations are inside explicit button guards."""

import hashlib
import os
from pathlib import Path
import sqlite3

import streamlit as st

from . import ui_service as service


def paper_label(row):
    title = service.display_text(row.get("title"), "标题缺失")
    return f"{title} · {row.get('year') or '年份未知'} · {service.author_names(row)} · {row['paperId'][:8]}"


def operation_button(operation, kind, label, retry_label, enabled):
    """A separate preparation click arms a new attempt, never sends HTTP itself."""
    attempt = operation["attempt"]
    key = f"{kind}_{operation['fingerprint']}_{attempt}"
    clicked = st.button(label, key=key, disabled=not enabled or operation["status"] != "ready")
    if operation["status"] != "ready":
        state = {"running": "进行中", "succeeded": "已完成", "failed": "失败或中断"}[operation["status"]]
        st.caption(f"本组参数：{state}。重复提交不会发起新请求。")
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
    # Drop the old visible result first: a failed open must not masquerade as success.
    st.session_state.pop("view", None)
    st.session_state["view"] = service.load_view(db_path, run_id)


def entry_panel():
    with st.sidebar:
        st.header("开始探索")
        if st.button("打开演示（无需网络）", key="open_demo", type="primary"):
            st.session_state.pop("view", None)
            with st.spinner("正在打开本地演示…"):
                activate(*service.open_demo(service.ROOT / "output/ui-demo"))
        st.caption("演示包含 2 篇合成种子、两类候选；无需 API Key。")
        mode = st.radio("数据入口", ["已有样本", "新建采集"], key="entry_mode")
        if mode == "已有样本":
            path = st.text_input("数据库路径", value="papertrail.db", key="open_path")
            if st.button("读取任务列表", key="list_runs"):
                st.session_state.pop("run_list", None)
                st.session_state["run_list"] = (path, service.list_runs(path))
            listing = st.session_state.get("run_list")
            if listing and listing[0] == path:
                rows = {row["run_id"]: row for row in listing[1]}
                choice = st.selectbox("已保存任务", list(rows), index=None,
                                      format_func=lambda rid: f"#{rid} · {rows[rid]['status']} · {rows[rid]['source']}",
                                      key="saved_run")
                if not rows:
                    st.info("该数据库尚无采集任务。")
                if st.button("打开选定样本", disabled=choice is None, key="open_saved"):
                    activate(path, choice)
            with st.form("open_run"):
                run_id = st.number_input("或直接输入 run_id", min_value=1, max_value=2**53-1, value=1, step=1)
                if st.form_submit_button("按 run_id 打开"):
                    activate(path, int(run_id))
        else:
            collection_panel()


def collection_panel():
    st.caption("仅明确点击查询／采集时联网；需先获得 Owner 授权。Key 只从环境变量读取。")
    text = st.text_area("种子 DOI、arXiv ID 或标题（每行一篇，1–3 篇）", key="seed_text")
    authorized = st.checkbox("我已获 Owner 授权本次联网查询与采集", key="authorized")
    query_operation = service.operation_state(st.session_state, "query", text)
    submit, attempt = operation_button(query_operation, "query_seeds", "查询种子候选（联网）",
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
    budget = st.number_input("全局被引记录预算", min_value=0, max_value=200, value=150, step=10, key="citation_budget")
    requests = st.number_input("最大请求尝试次数（含重试）", min_value=1, max_value=100, value=100, step=1,
                               key="request_budget")
    st.caption("预算由种子平分；按 API 顺序采样。参考文献每种子上限 10000，缓存有效期 7 天。")
    # Include input, confirmed results, absolute database path and all UI budgets.
    # Keep old signatures in the ledger when settings change and change back.
    parameters = (text, tuple(confirmed[1]) if valid else None, tuple(confirmed[2]) if valid else None,
                  os.path.normcase(str(Path(path).resolve())), int(budget), int(requests))
    collection_operation = service.operation_state(st.session_state, "collection", parameters)
    submit, attempt = operation_button(collection_operation, "collect", "开始采集（联网）",
                                        "准备重新采集／重试", authorized and valid)
    if submit:
        st.session_state.pop("view", None)
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
            st.error("采集未完成，不能分析为有效样本。已保存成功页面；请检查下方错误与请求预算。")
        st.caption(f"最近任务 #{report['run_id']} · {report['status']} · 请求尝试 {report['request_attempts']}")
        with st.expander("采集状态与错误详情"):
            st.json(report)


def show_table(rows, seed_count, demo):
    if not rows:
        st.info("没有符合当前筛选的论文。可关闭年份或引用量筛选以保留未知值。")
        return
    st.dataframe(service.table_rows(rows, seed_count, demo), hide_index=True,
                 column_config={"来源链接": st.column_config.LinkColumn("来源链接", display_text="打开来源"),
                                **{name: st.column_config.NumberColumn(name, format="%d") for name in
                                   ("年份", "来源全局引用量", "样本入度", "样本出度", "名次", "同值篇数（含自身）")}})


def detail(view, paper_id, prefix, reading):
    row = view["rows"][paper_id]
    result = view["result"]
    demo = result["sample"]["mode"] == "offline_fixture"
    st.subheader("论文详情")
    st.text(service.display_text(row.get("title"), "标题缺失"))
    st.caption("演示数据 · 合成论文，非真实研究成果" if demo else row["source"])
    st.text(f"年份：{row.get('year') if row.get('year') is not None else '未知'}")
    st.text(service.author_names(row))
    st.text(service.display_text(row.get("abstract"), "摘要缺失"))
    st.text(service.relation_text(row, result["statistics"]["seed_count"]))
    st.caption(f"样本入度 {row['local_in_degree']} · 样本出度 {row['local_out_degree']} · "
               f"来源全局引用量 {row['global_citation_count'] if row['global_citation_count'] is not None else '未知'}")
    if link := service.source_link(row, demo):
        st.link_button("查看论文来源", link)
    elif demo:
        st.caption("合成数据不提供真实论文链接；外部 ID 也仅为演示占位。")
    if st.button("将此论文加入阅读清单", key=prefix + "add_node"):
        if paper_id not in reading["ids"]:
            reading["ids"].append(paper_id)
        st.session_state[prefix + "reading_widget"] = list(reading["ids"])
        st.success("已加入下方阅读清单。")


def graph_panel(view, prefix, reading):
    st.caption("箭头 A → B 表示 A 引用 B。橙色菱形是种子；蓝色圆点是非种子。点击节点，或用下拉框选择。")
    limit = st.slider("网络显示节点上限", min_value=3, max_value=200, value=100, step=1, key=prefix + "limit")
    figure, visible = service.cached_network_figure(view, limit)
    st.caption(f"图中显示 {len(visible)}/{view['graph'].number_of_nodes()} 篇；优先显示种子，其余按 ID。"
               "此显示限制不改变底层样本、候选与统计。")
    selection_key = prefix + "node"
    def on_node_selection():
        selected = service.selected_node(st.session_state.get(prefix + "network"), visible)
        if selected:
            st.session_state[selection_key] = selected
    st.plotly_chart(figure, key=prefix + "network", on_select=on_node_selection, selection_mode="points",
                    config={"displaylogo": False, "scrollZoom": False})
    rows = view["rows"]
    chosen = st.selectbox("选择论文节点查看详情", list(rows), format_func=lambda pid: paper_label(rows[pid]),
                          key=selection_key)
    detail(view, chosen, prefix, reading)
    with st.expander("核对图中引用方向"):
        st.dataframe([{"引用者": service.display_text(rows[a].get("title"), a),
                       "被引用者": service.display_text(rows[b].get("title"), b)}
                      for a, b in view["graph"].subgraph(visible).edges], hide_index=True)


def candidates_panel(view, prefix):
    stats = view["result"]["statistics"]
    demo = view["result"]["sample"]["mode"] == "offline_fixture"
    options = {}
    col1, col2 = st.columns(2)
    with col1:
        use_year = st.checkbox("启用年份筛选", key=prefix + "use_year")
        options["year_min"] = st.number_input("最早年份", min_value=0, max_value=9999, value=1900, step=1,
                                               disabled=not use_year, key=prefix + "year_min") if use_year else None
        options["year_max"] = st.number_input("最晚年份", min_value=0, max_value=9999, value=2100, step=1,
                                               disabled=not use_year, key=prefix + "year_max") if use_year else None
    with col2:
        use_count = st.checkbox("启用来源全局引用量筛选", key=prefix + "use_count")
        options["citation_min"] = st.number_input("最少引用量", min_value=0, value=0, step=1,
                                                   key=prefix + "citation_min") if use_count else None
        options["citation_max"] = st.number_input("最多引用量", min_value=0, value=1000000, step=1,
                                                   key=prefix + "citation_max") if use_count else None
    keyword = st.text_input("关键词（逗号分隔，搜索标题和摘要，须全部命中）", key=prefix + "keywords")
    options["keywords"] = [term.strip() for term in keyword.replace("，", ",").split(",") if term.strip()]
    sort = st.selectbox("排序依据（降序）", ["分组关系数量"] + list(service.SORT_LABELS.values()), key=prefix + "sort")
    options["sort_by"] = next((key for key, value in service.SORT_LABELS.items() if value == sort), None)
    st.caption("同值论文并列；同值篇数包含自身，paperId 仅稳定显示顺序，不代表研究价值。未知排序值置后且不排名。"
               "启用数值筛选会排除该字段缺失的论文；统计仍针对筛选前样本。")
    try:
        groups = service.candidate_groups(view, **options)
    except ValueError as error:
        st.error(service.safe(str(error)))
        return
    for category, rows in groups.items():
        st.subheader(f"{service.CATEGORY_LABELS[category]} · {len(rows)} 篇")
        show_table(rows, stats["seed_count"], demo)
    st.subheader("种子论文（单独展示，不参与候选筛选排名）")
    show_table(view["result"]["seeds"], stats["seed_count"], demo)


def statistics_panel(view, prefix):
    stats = view["result"]["statistics"]
    st.info("以下仅为当前采集样本统计，不是研究领域整体趋势；不受候选筛选或网络显示数量影响。")
    keywords = st.text_input("统计关键词（逗号分隔，逐词统计标题与摘要）", key=prefix + "statistics_keywords")
    st.caption("仅代表当前采集样本，不代表整个研究领域。忽略大小写，按字面子串匹配；每篇论文计一次，"
               "标题或摘要为去重并集，不是两列相加。包含种子及所有候选，不受候选筛选影响。")
    counts = service.keyword_statistics(view, keywords)
    if counts:
        st.dataframe([{"关键词": row["keyword"], "标题出现论文数": row["title_paper_count"],
                       "摘要出现论文数": row["abstract_paper_count"],
                       "标题或摘要出现论文数": row["either_paper_count"]} for row in counts], hide_index=True)
    if stats["year_distribution"]:
        st.bar_chart([{"年份": row["year"], "样本论文数": row["paper_count"]}
                      for row in stats["year_distribution"]], x="年份", y="样本论文数")
    else:
        st.info("所有论文年份未知，无法绘制年份分布。")
    st.dataframe(stats["year_distribution"], hide_index=True,
                 column_config={"year": st.column_config.NumberColumn("年份", format="%d"),
                                "paper_count": "样本论文数"})
    st.subheader("数据质量：缺失字段的论文数")
    names = {"year": "年份", "title": "标题", "abstract": "摘要", "global_citation_count": "全局引用量",
             "url": "原始 URL", "authors": "作者", "externalIds": "外部 ID"}
    st.dataframe([{"字段": names[key], "缺失论文数": value} for key, value in stats["missing_metadata"].items()], hide_index=True)
    st.caption(f"两类候选重叠 {stats['both_category_count']} 篇，因此两类数量不能直接相加。")
    with st.expander("采集覆盖、警告与方法局限"):
        sample = view["result"]["sample"]
        st.json(sample["coverage"])
        st.json(sample["direction_states"])
        for message in sample["limitations"]:
            st.write(message)


def reading_panel(view, prefix, reading):
    st.caption("手动选择种子或候选。切换筛选、标签页、样本后仍按各自样本保存选择；关闭或刷新浏览器可能结束会话，请先下载。")
    st.caption("阅读理由输入后按 Enter 或点击空白处，等待保存后再下载。")
    key = prefix + "reading_widget"
    if key not in st.session_state:
        st.session_state[key] = list(reading["ids"])
    rows = view["rows"]
    selected = st.multiselect("阅读清单论文", list(rows), key=key, format_func=lambda pid: paper_label(rows[pid]))
    reading["ids"] = list(selected)
    for index, paper_id in enumerate(selected, 1):
        note_key = prefix + "note_" + paper_id
        if note_key not in st.session_state:
            st.session_state[note_key] = reading["notes"].get(paper_id, "")
        st.text(service.display_text(rows[paper_id].get("title"), "标题缺失"))
        reading["notes"][paper_id] = st.text_input(f"阅读理由（第 {index} 篇）",
                                                  max_chars=500, key=note_key)
    csv_data, markdown = service.export_reading_list(view, selected, reading["notes"])
    name = f"papertrail-run-{view['identity'][1]}"
    st.download_button("下载 CSV", csv_data, file_name=name + ".csv", mime="text/csv",
                       disabled=not selected, key=prefix + "csv", on_click="ignore")
    st.download_button("下载 Markdown", markdown, file_name=name + ".md", mime="text/markdown",
                       disabled=not selected, key=prefix + "md", on_click="ignore")
    st.caption(f"已选择 {len(selected)} 篇。导出包含来源、run_id、关系依据及阅读理由。")


def main():
    st.set_page_config(page_title="PaperTrail · 文献探索", page_icon="📚", layout="wide")
    st.title("PaperTrail · 从种子论文到阅读清单")
    st.caption("一层引用采样 · 可检查的关系依据 · 手动决定阅读什么")
    try:
        entry_panel()
    except (ValueError, OSError, sqlite3.Error) as error:
        st.error("操作失败，未打开新样本：" + service.safe(str(error)) if isinstance(error, ValueError)
                 else "本地文件或数据库访问失败，请检查路径、权限和数据库格式。")
    view = st.session_state.get("view")
    if not view:
        st.info("点击左侧“打开演示”即可离线体验完整流程，也可以打开已有样本或新建采集。失败或未完成的样本不能分析。")
        return
    sample, stats = view["result"]["sample"], view["result"]["statistics"]
    st.subheader(f"当前样本 · run #{sample['run_id']}")
    st.caption(f"来源：{sample['source']} · 状态：{sample['status']} · 采集范围：{sample['collection_scope']}")
    if sample["mode"] == "offline_fixture":
        st.warning("演示数据：所有论文与引用量均为合成内容，不是真实研究成果。")
    if sample["collection_scope"] == "budget_truncated":
        st.warning("正常预算截断（bounded）：已达到采样配额。以下是有限样本，按 API 顺序获取，不是最重要的 N 篇。")
        with st.expander("预算截断方向与数量"):
            st.dataframe(sample["truncated_directions"], hide_index=True)
    coverage = sample["coverage"]
    st.caption(f"数据质量：{coverage['unresolved_records']} 条原始关系记录缺少论文 ID，未纳入图；"
               f"{coverage['missing_abstract_records']} 条原始记录缺摘要（此处含重复记录）。")
    if sample["warnings"]:
        with st.expander("查看全部采集警告（保留原始说明）"):
            for warning in sample["warnings"]:
                st.warning(warning)
    st.caption("单层采样可能缺少候选之间的引用；来源全局引用量与样本入度不同。元数据是打开样本时的最新已知值。")
    for column, label, value in zip(st.columns(5), ["样本论文", "引用边", "种子", "非种子候选", "缺失年份"],
                                    [stats["paper_count"], view["graph"].number_of_edges(), stats["seed_count"],
                                     stats["candidate_count"], stats["missing_metadata"]["year"]]):
        column.metric(label, value)
    prefix = hashlib.sha256(repr(view["identity"]).encode()).hexdigest()[:16] + "_"
    reading = st.session_state.setdefault("reading_lists", {}).setdefault(view["identity"], {"ids": [], "notes": {}})
    tabs = st.tabs(["引用网络", "候选论文", "样本统计", "阅读清单"])
    with tabs[0]:
        graph_panel(view, prefix, reading)
    with tabs[1]:
        candidates_panel(view, prefix)
    with tabs[2]:
        statistics_panel(view, prefix)
    with tabs[3]:
        reading_panel(view, prefix, reading)
