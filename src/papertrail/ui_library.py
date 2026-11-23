"""Reading-library widgets. Every edit is committed before the next render."""

import hashlib
import sqlite3

import streamlit as st

from . import library, ui_service as service


def confirm_remove(path, paper_id, key):
    # Bind confirmation to this paper, so changing selection cannot reuse it.
    with st.popover("移出", key=key + "_popover_" + paper_id, on_change="rerun"):
        st.text("将移出这篇论文及其备注、阅读状态和来源记录。")
        if st.button("确认移出", key=key + "_confirm_" + paper_id):
            library.remove(path, paper_id)
            st.rerun()


def favorite(view, paper_id, key):
    path = library.path_for(view)
    saved = next((r for r in library.items(path) if r["paper_id"] == paper_id), None)
    try:
        if saved:
            st.text("✓ 已在阅读库 · " + library.STATUS_LABELS[saved["status"]])
            # Re-discovery is an explicit action; rendering never adds provenance.
            identity = tuple(view["identity"])
            if not any((s["db_path"], s["run_id"]) == identity for s in saved["sources"]):
                if st.button("记录本次探索", key=key + "_source"):
                    library.add(path, view, paper_id)
                    st.rerun()
            confirm_remove(path, paper_id, key + "_remove")
        elif st.button("加入阅读库", key=key):
            library.add(path, view, paper_id)
            st.rerun()
    except (ValueError, OSError, sqlite3.Error):
        st.error("阅读库保存失败，请检查文件权限和可用空间后重试。")


def apply_editor_changes(path, key, rows, prefix):
    """Map row offsets against the rendered snapshot, never a newly filtered table."""
    status_codes = {v: k for k, v in library.STATUS_LABELS.items()}
    priority_codes = {v: k for k, v in library.PRIORITY_LABELS.items()}
    try:
        event = st.session_state.get(key, {})
        if event.get("added_rows") or event.get("deleted_rows"):
            raise ValueError("请使用收藏或移出按钮修改阅读库条目。")
        changes = {}
        for index, edits in event.get("edited_rows", {}).items():
            index = int(index)
            if not 0 <= index < len(rows) or not set(edits) <= {"查看", "状态", "优先级", "备注"}:
                raise ValueError("无效的表格修改，请刷新后重试。")
            row = rows[index]
            if edits.get("查看"):
                st.session_state[prefix + "selected"] = row["paper_id"]
            fields = {}
            if "状态" in edits:
                if edits["状态"] not in status_codes:
                    raise ValueError("请选择有效的阅读状态。")
                fields["status"] = status_codes[edits["状态"]]
            if "优先级" in edits:
                if edits["优先级"] not in (None, "") and edits["优先级"] not in priority_codes:
                    raise ValueError("请选择有效的优先级。")
                fields["priority"] = priority_codes.get(edits["优先级"])
            if "备注" in edits:
                fields["note"] = edits["备注"] or ""
            fields = {k: v for k, v in fields.items() if row[k] != v}
            if fields:
                changes[row["paper_id"]] = fields
        if changes:
            library.update_many(path, changes)
    except (ValueError, OSError, sqlite3.Error):
        st.session_state["library_error"] = "修改未保存，请检查输入（备注最多 2000 字）和阅读库权限后重试。"
    finally:
        # Retire the old delta so filtering/reordering cannot apply it to another row.
        st.session_state[prefix + "revision"] = st.session_state.get(prefix + "revision", 0) + 1


def panel(view=None):
    path = library.path_for(view)
    prefix = "library_" + hashlib.sha256(str(path).encode()).hexdigest()[:12] + "_"
    all_rows = library.items(path)
    st.subheader(f"阅读库 · {len(all_rows)} 篇")
    if view and view["result"]["sample"]["mode"] == "offline_fixture":
        st.badge("演示阅读库", color="gray")
    if error := st.session_state.pop("library_error", None):
        st.error(error)
    if not all_rows:
        st.text('在引用网络或候选论文中点“加入阅读库”。')
        st.caption(str(path.resolve()))
        return
    status, current, search = st.columns([3, 2, 3], vertical_alignment="bottom")
    label = status.segmented_control("阅读状态", ["全部", *library.STATUS_LABELS.values()], default="全部",
                                     required=True, key=prefix + "status", label_visibility="collapsed")
    only_current = current.checkbox("只看当前样本", key=prefix + "current", disabled=view is None)
    query = search.text_input("搜索阅读库", placeholder="搜索标题或备注", key=prefix + "search",
                              label_visibility="collapsed")
    code = next((k for k, v in library.STATUS_LABELS.items() if v == label), None)
    rows = library.items(path, status=code, query=query,
                         identity=view["identity"] if view and only_current else None)
    csv_data, markdown = library.export_items(rows)
    csv_column, md_column = st.columns(2)
    csv_column.download_button("导出 CSV", csv_data, file_name="papertrail-library.csv", mime="text/csv",
                               disabled=not rows, key=prefix + "csv", on_click="ignore")
    md_column.download_button("导出 Markdown", markdown, file_name="papertrail-library.md", mime="text/markdown",
                              disabled=not rows, key=prefix + "md", on_click="ignore")
    if rows:
        selected = st.session_state.get(prefix + "selected")
        data = [{"查看": r["paper_id"] == selected, "标题": r["title"], "年份": r["year"],
                 "状态": library.STATUS_LABELS[r["status"]], "优先级": library.PRIORITY_LABELS.get(r["priority"], ""),
                 "备注": r["note"], "来源": f"{len(r['sources'])} 次探索"} for r in rows]
        signature = repr((label, only_current, query, [r["paper_id"] for r in rows],
                          st.session_state.get(prefix + "revision", 0)))
        key = prefix + "editor_" + hashlib.sha256(signature.encode()).hexdigest()[:12]
        st.data_editor(data, hide_index=True, num_rows="fixed", key=key,
                       on_change=apply_editor_changes, args=(path, key, rows, prefix),
                       disabled=["标题", "年份", "来源"], width="stretch",
                       column_config={
                           "查看": st.column_config.CheckboxColumn(width="small", help="勾选一行查看来源和原文。"),
                           "标题": st.column_config.TextColumn(width="medium"),
                           "年份": st.column_config.NumberColumn(format="%d"),
                           "状态": st.column_config.SelectboxColumn(options=list(library.STATUS_LABELS.values()), required=True),
                           "优先级": st.column_config.SelectboxColumn(options=["", *library.PRIORITY_LABELS.values()]),
                           "备注": st.column_config.TextColumn(max_chars=2000, width="medium")})
        selected_row = next((r for r in rows if r["paper_id"] == selected), None)
        if selected_row:
            with st.container(border=True):
                st.text(selected_row["title"])
                for source in selected_row["sources"]:
                    st.text(f"来自：{source['seed_titles']} · #{source['run_id']} · {source['added_at'][:10]}")
                    st.text(source["category"] + " · " + source["relation"])
                original, remove = st.columns(2)
                link = selected_row["source_url"]
                if not any(s["demo"] for s in selected_row["sources"]) and service.valid_source_url(link):
                    original.link_button("打开原文", link, type="primary")
                with remove:
                    confirm_remove(path, selected, prefix + "remove")
    else:
        st.text("没有符合条件的论文。")
    st.caption(str(path.resolve()))
