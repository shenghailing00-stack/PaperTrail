"""Personal reading library: independent SQLite snapshots, no sample writes or HTTP."""

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import io
import json
from pathlib import Path
import sqlite3

from . import ui_service as service

STATUS_LABELS = {"to_read": "待读", "reading": "在读", "read": "已读"}
PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def path_for(view=None):
    demo = view is not None and view["result"]["sample"]["mode"] == "offline_fixture"
    return service.ROOT / ("output/ui-demo/library-demo.db" if demo else "output/library.db")


@contextmanager
def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version not in (0, 1) or (tables and tables != {"library_items", "library_sources"}):
            raise ValueError("这不是支持的阅读库文件，未修改其内容。")
        if not tables:
            with connection:
                connection.execute("""CREATE TABLE library_items (
                    paper_id TEXT PRIMARY KEY, title TEXT, year INTEGER, authors_json TEXT, source_url TEXT,
                    status TEXT NOT NULL DEFAULT 'to_read' CHECK(status IN ('to_read','reading','read')),
                    priority TEXT CHECK(priority IN ('high','medium','low')),
                    note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=2000),
                    added_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE library_sources (
                    paper_id TEXT NOT NULL REFERENCES library_items(paper_id) ON DELETE CASCADE,
                    db_path TEXT NOT NULL, run_id INTEGER NOT NULL, seed_titles TEXT,
                    category TEXT, relation TEXT, added_at TEXT NOT NULL,
                    sample_status TEXT NOT NULL, collection_scope TEXT NOT NULL, demo INTEGER NOT NULL,
                    PRIMARY KEY(paper_id,db_path,run_id))""")
                connection.execute("PRAGMA user_version = 1")
        elif version != 1:
            raise ValueError("阅读库版本不受支持，未修改其内容。")
        yield connection
    finally:
        connection.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def add(path, view, paper_id):
    """Validate membership before opening the library; upsert metadata atomically."""
    if paper_id not in view["rows"]:
        raise ValueError("只能收藏当前样本中的论文。")
    if Path(path).resolve() == Path(view["identity"][0]).resolve():
        raise ValueError("阅读库不能使用样本数据库。")
    row = view["rows"][paper_id]
    sample = view["result"]["sample"]
    demo = sample["mode"] == "offline_fixture"
    authors = row.get("authors")
    authors = [service.display_text(a.get("name")) for a in authors if isinstance(a, dict)] if isinstance(authors, list) else []
    link = service.safe(service.source_link(row, demo))
    link = link if service.valid_source_url(link) else None
    now = _now()
    categories = {"shared_reference": "前期工作", "shared_citing": "后续研究"}
    category = "种子" if row["is_seed"] else " / ".join(categories[c] for c in row["categories"])
    source = service.safe([paper_id, view["identity"][0], view["identity"][1],
        " · ".join(service.display_text(r.get("title"), "标题缺失").split(":")[0].split("：")[0]
                   for r in view["result"]["seeds"]), category,
        service.relation_text(row, view["result"]["statistics"]["seed_count"]), now,
        sample["status"], sample["collection_scope"], int(demo)])
    metadata = service.safe([paper_id, service.display_text(row.get("title"), "标题缺失"), row.get("year"),
                             json.dumps(service.safe(authors), ensure_ascii=False), link, now, now])
    with _connect(path) as connection, connection:
        if connection.execute("SELECT 1 FROM library_sources WHERE demo != ? LIMIT 1", (int(demo),)).fetchone():
            raise ValueError("演示和真实论文必须使用独立阅读库。")
        connection.execute("""INSERT INTO library_items
            (paper_id,title,year,authors_json,source_url,added_at,updated_at) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(paper_id) DO UPDATE SET title=excluded.title, year=excluded.year,
            authors_json=excluded.authors_json, source_url=excluded.source_url, updated_at=excluded.updated_at""", metadata)
        connection.execute("""INSERT INTO library_sources
            (paper_id,db_path,run_id,seed_titles,category,relation,added_at,sample_status,collection_scope,demo)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(paper_id,db_path,run_id) DO NOTHING""", source)


def update_many(path, changes):
    """Only changed user fields; validate the whole batch before committing any row."""
    validated = []
    for paper_id, fields in changes.items():
        if not fields or not set(fields) <= {"status", "priority", "note"}:
            raise ValueError("只允许修改状态、优先级和备注。")
        if "status" in fields and fields["status"] not in STATUS_LABELS:
            raise ValueError("无效的阅读状态。")
        if "priority" in fields and fields["priority"] is not None and fields["priority"] not in PRIORITY_LABELS:
            raise ValueError("无效的优先级。")
        if "note" in fields and (not isinstance(fields["note"], str) or len(fields["note"]) > 2000):
            raise ValueError("备注最多 2000 字。")
        validated.append((service.safe(paper_id), service.safe(fields)))
    with _connect(path) as connection, connection:
        for paper_id, fields in validated:
            query = ", ".join(f"{key}=?" for key in fields)
            cursor = connection.execute(f"UPDATE library_items SET {query}, updated_at=? WHERE paper_id=?",
                                        [*fields.values(), _now(), paper_id])
            if cursor.rowcount != 1:
                raise ValueError("论文已不在阅读库中，请刷新后重试。")


def remove(path, paper_id):
    with _connect(path) as connection, connection:
        connection.execute("DELETE FROM library_items WHERE paper_id=?", (service.safe(paper_id),))


def items(path, *, status=None, query="", identity=None):
    """One consistent read including sources; no connection survives the call."""
    if not Path(path).exists():
        return []
    with _connect(path) as connection:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("SELECT * FROM library_items ORDER BY added_at,paper_id")]
        sources = {}
        for row in connection.execute("SELECT * FROM library_sources ORDER BY added_at,db_path,run_id"):
            sources.setdefault(row["paper_id"], []).append(dict(row))
    result = []
    for row in rows:
        row["sources"] = sources.get(row["paper_id"], [])
        if status and row["status"] != status:
            continue
        if query.casefold() not in ((row["title"] or "") + "\n" + row["note"]).casefold():
            continue
        if identity and not any((s["db_path"], s["run_id"]) == tuple(identity) for s in row["sources"]):
            continue
        result.append(service.safe(row))
    return result


def export_items(rows):
    """Export the filtered snapshot with the same escaping as the legacy exporter."""
    headers = ["标题", "年份", "来源链接", "状态", "优先级", "备注", "发现于", "关系说明", "paperId"]
    records, scopes = [], {}
    for row in rows:
        sources = row["sources"]
        demo = any(s["demo"] for s in sources)
        records.append([row["title"], row["year"] if row["year"] is not None else "",
                        row["source_url"] if not demo and service.valid_source_url(row["source_url"]) else "",
                        STATUS_LABELS[row["status"]], PRIORITY_LABELS.get(row["priority"], ""), row["note"],
                        "；".join(("演示数据（合成） · " if s["demo"] else "") +
                                  f"run #{s['run_id']} · {s['seed_titles']}" for s in sources),
                        "；".join(s["relation"] for s in sources), row["paper_id"]])
        for source in sources:
            if source["sample_status"] == "bounded":
                scopes[(source["db_path"], source["run_id"])] = source
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows([[service._cell(value) for value in record] for record in records])
    lines = ["# PaperTrail 阅读库", "", "依据 PaperTrail 采集样本与个人选择，不代表领域整体或必读推荐。", ""]
    if any(s["demo"] for row in rows for s in row["sources"]):
        lines.append("演示数据（合成，非真实研究成果）")
    for source in scopes.values():
        lines.append("采集范围：" + service._md(
            f"run #{source['run_id']} · {source['seed_titles']} · {Path(source['db_path']).name} · {source['collection_scope']}"))
    lines += ["", "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines += [service._markdown_record(record) for record in records]
    return buffer.getvalue().encode("utf-8-sig"), "\n".join(lines).encode("utf-8")
