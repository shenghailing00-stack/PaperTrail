"""Library acceptance: all databases live in tmp_path; HTTP is forbidden."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
from unittest.mock import Mock

import pytest
import requests

from papertrail import library, ui_service as service
from papertrail.cli import FixtureClient
from papertrail.collector import collect
from papertrail.store import PaperStore


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("Library must stay offline")))


@pytest.fixture
def view(tmp_path):
    return service.load_view(*service.open_demo(tmp_path / "sample"))


def test_schema_restart_and_source_cascade(view):
    path = library.path_for(view)
    pid = next(iter(view["rows"]))
    library.add(path, view, pid)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"library_items", "library_sources"}
    library.update_many(path, {pid: {"status": "reading", "priority": "high", "note": "重新打开仍保留"}})
    # Every call already closes its connection; no in-memory state is needed.
    assert library.items(Path(str(path)))[0]["note"] == "重新打开仍保留"
    library.remove(path, pid)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM library_sources").fetchone()[0] == 0
    assert library.items(path) == []


def test_two_runs_update_metadata_preserve_user_fields(view):
    path = library.path_for(view)
    pid = next(iter(view["rows"]))
    library.add(path, view, pid)
    library.update_many(path, {pid: {"status": "read", "priority": "low", "note": "我的判断"}})
    before = library.items(path)[0]
    with PaperStore(view["identity"][0]) as store:
        report = collect(FixtureClient(service.FIXTURE), store, service.DEMO_SEEDS,
                         citation_budget=1, page_size=2, request_budget=100)
    second = service.load_view(view["identity"][0], report["run_id"])
    assert second["identity"] != view["identity"]
    second["rows"][pid].update(title="新标题", year=2030, authors=[{"name": "新作者"}])
    library.add(path, second, pid)
    library.add(path, second, pid)
    rows = library.items(path)
    assert len(rows) == 1 and len(rows[0]["sources"]) == 2
    assert rows[0]["title"] == "新标题" and rows[0]["year"] == 2030
    assert json.loads(rows[0]["authors_json"]) == ["新作者"]
    for field in ("status", "priority", "note", "added_at"):
        assert rows[0][field] == before[field]


@pytest.mark.parametrize("fields", [{"status": "bad"}, {"status": None}, {"priority": "urgent"},
    {"note": "字" * 2001}, {"note": 5}, {"title": "cannot edit snapshot"}])
def test_invalid_edits_are_rejected(view, fields):
    pid = next(iter(view["rows"]))
    path = library.path_for(view)
    library.add(path, view, pid)
    before = library.items(path)
    with pytest.raises(ValueError):
        library.update_many(path, {pid: fields})
    assert library.items(path) == before


def test_sample_sha256_unchanged_after_each_operation(view):
    sample = Path(view["identity"][0])
    digest = lambda: hashlib.sha256(sample.read_bytes()).hexdigest()
    before = digest()
    path = library.path_for(view)
    pid = next(iter(view["rows"]))
    library.add(path, view, pid)
    assert digest() == before
    library.update_many(path, {pid: {"note": "备注", "status": "reading", "priority": "medium"}})
    assert digest() == before
    library.remove(path, pid)
    assert digest() == before
    with pytest.raises(ValueError):
        library.add(sample, view, pid)
    with pytest.raises(ValueError):
        library.update_many(sample, {pid: {"note": "must not write"}})
    assert digest() == before


def test_foreign_membership_and_atomic_rollback(view):
    path = library.path_for(view)
    with pytest.raises(ValueError):
        library.add(path, view, "foreign")
    assert not path.exists()
    pid = next(iter(view["rows"]))
    library.add(path, view, pid)
    with pytest.raises(ValueError):
        library.update_many(path, {pid: {"note": "rollback"}, "foreign": {"note": "bad"}})
    assert library.items(path)[0]["note"] == ""
    broken = copy.deepcopy(view)
    broken["identity"] = (view["identity"][0], None)
    broken["rows"][pid]["title"] = "rollback title"
    with pytest.raises(sqlite3.IntegrityError):
        library.add(path, broken, pid)
    assert library.items(path)[0]["title"] == view["rows"][pid]["title"]


def test_real_demo_isolation_and_filtering(view):
    real = copy.deepcopy(view)
    real["result"]["sample"]["mode"] = "api"
    pid = next(iter(view["rows"]))
    assert library.path_for(view) != library.path_for(real) == library.path_for()
    library.add(library.path_for(view), view, pid)
    library.add(library.path_for(real), real, pid)
    library.update_many(library.path_for(real), {pid: {"note": "Real note", "status": "reading"}})
    assert library.items(library.path_for(view))[0]["source_url"] is None
    assert library.items(library.path_for(view))[0]["note"] == ""
    assert len(library.items(library.path_for(real), status="reading", query="NOTE", identity=real["identity"])) == 1
    assert not library.items(library.path_for(real), status="read")
    assert not library.items(library.path_for(real), identity=(real["identity"][0], 999))


def test_all_text_redacted_before_storage(view, monkeypatch):
    secret = "task006-secret-for-test"
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", secret)
    pid = next(iter(view["rows"]))
    view["rows"][pid].update(title=secret, authors=[{"name": secret}])
    view["result"]["sample"]["collection_scope"] = secret
    path = library.path_for(view)
    library.add(path, view, pid)
    library.update_many(path, {pid: {"note": secret}})
    assert secret.encode() not in path.read_bytes()
    assert secret not in json.dumps(library.items(path))


def test_export_safety_filter_scope_and_offline_snapshot(view):
    pid = next(iter(view["rows"]))
    path = library.path_for(view)
    view["rows"][pid]["title"] = '=SUM(1,2) | ![x](https://bad.invalid) `tick`'
    view["result"]["sample"]["status"] = "bounded"
    library.add(path, view, pid)
    library.update_many(path, {pid: {"note": '@公式\n<img src="https://bad.invalid">', "status": "read"}})
    rows = library.items(path, status="read")
    # Malformed externally modified links must also be rejected at export time.
    rows[0]["source_url"] = "javascript:alert(1)"
    csv_bytes, markdown = library.export_items(rows)
    record = next(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    assert record["标题"].startswith("'=") and record["备注"].startswith("'@")
    assert record["来源链接"] == "" and record["状态"] == "已读"
    text = markdown.decode()
    assert "依据 PaperTrail 采集样本与个人选择，不代表领域整体或必读推荐。" in text
    assert "采集范围：" in text and f"run #{view['identity'][1]}" in text
    assert "演示数据" in text
    outside = re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", "", text.splitlines()[-1])
    assert "![" not in outside and "<img" not in outside and "bad.invalid" not in outside
    rows[0]["sources"][0]["demo"] = 0
    rows[0]["source_url"] = "https://example.org/paper_(one)?q=中文"
    assert b"[\xe6\x9f\xa5\xe7\x9c\x8b\xe6\x9d\xa5\xe6\xba\x90](<https://example.org/paper_%28one%29?q=" in library.export_items(rows)[1]
    assert len(list(csv.DictReader(io.StringIO(library.export_items([])[0].decode("utf-8-sig"))))) == 0


def test_legacy_export_bytes_unchanged():
    fixture = Path(__file__).parent / "fixtures"
    payload = json.loads((fixture / "reading_export_legacy.json").read_text(encoding="utf-8"))
    result = service.export_reading_list(payload["view"], payload["selected"], payload["notes"])
    for extension, actual in zip(("csv", "md"), result):
        assert actual == (fixture / ("reading_export_legacy." + extension)).read_bytes()


def test_refuse_mixing_demo_and_real_in_one_library(view):
    path = library.path_for(view)
    pid = next(iter(view["rows"]))
    library.add(path, view, pid)
    before = library.items(path)
    real = copy.deepcopy(view)
    real["result"]["sample"]["mode"] = "api"
    with pytest.raises(ValueError, match="独立阅读库"):
        library.add(path, real, pid)
    assert library.items(path) == before


def test_markdown_scope_uses_filename_without_sample_directory(view):
    path = library.path_for(view)
    pid = next(iter(view["rows"]))
    view["result"]["sample"]["status"] = "bounded"
    library.add(path, view, pid)
    rows = library.items(path)
    markdown = library.export_items(rows)[1].decode("utf-8")
    sample_path = Path(view["identity"][0])
    assert sample_path.name in markdown
    assert str(sample_path.parent) not in markdown
    assert str(sample_path) not in markdown
    assert str(sample_path.parent).replace("\\", "/") not in markdown
    assert rows[0]["sources"][0]["db_path"] == str(sample_path)
