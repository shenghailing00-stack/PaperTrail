"""Eight core groups; all HTTP and sleeps are fake, never use the owner's key."""

import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import ssl
from unittest.mock import Mock

import pytest
import requests

from scripts import api_spike as spike


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("Real HTTP forbidden")))
    clock = [0.0]
    monkeypatch.setattr(spike.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(spike.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


@pytest.fixture
def data():
    return json.loads((Path(__file__).parent / "fixtures" / "s2_spike.json").read_text(encoding="utf-8"))


def response(body=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = body
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def fake_client(monkeypatch, results, key=None):
    client = spike.S2Client(key)
    get = Mock(side_effect=results)
    monkeypatch.setattr(client.session, "get", get)
    return client, get


def test_success_fields_direction_deduplication_and_pacing(monkeypatch, data, offline):
    client, get = fake_client(monkeypatch, [response(data[k]) for k in ("metadata", "references", "citations")], "test-secret")
    starts = []
    replies = iter(get.side_effect)
    get.side_effect = lambda *a, **kw: (starts.append(offline[0]), next(replies))[1]
    report = spike.run_probe(client, "ARXIV:0000.00000", 7, 9)
    assert report["status"] == "ok" and report["http_attempts"] == 3
    assert all(b - a >= 1.1 for a, b in zip(starts, starts[1:]))
    calls = get.call_args_list
    assert set(calls[0].kwargs["params"]["fields"].split(",")) == set(spike.PAPER_FIELDS)
    assert calls[1].kwargs["params"] == {"fields": ",".join(spike.RELATION_FIELDS), "offset": 0, "limit": 7}
    assert calls[2].kwargs["params"]["limit"] == 9
    assert all(c.kwargs["timeout"] == (5, 20) and not c.kwargs["allow_redirects"] for c in calls)
    refs = report["results"]["references"]["summary"]
    cites = report["results"]["citations"]["summary"]
    assert refs["items"][0]["edge"] == ["seed-id", "ref-id"]
    assert cites["items"][0]["edge"] == ["citing-id", "seed-id"]
    assert (refs["returned"], refs["unresolved"], refs["unique_edges"], refs["duplicate_resolved_items"]) == (3, 1, 1, 1)
    assert refs["missing_paper_fields"]["abstract"] == 2
    assert refs["availability"]["contexts"] == {"available": 1, "empty": 1, "null": 1, "missing": 0}
    assert refs["availability"]["intents"] == {"available": 1, "empty": 0, "null": 1, "missing": 1}
    assert refs["availability"]["isInfluential"]["available"] == 2
    assert refs["truncated"] is False and cites["truncated"] is True


@pytest.mark.parametrize("payload,expected", [
    ({"data": []}, "end_reported_by_api"),
    ({"data": [], "next": None}, "unknown_next_value"),
    ({"data": [], "next": 0}, "unknown_next_value"),
    ({"data": [], "next": 5}, "more_available"),
    ({"data": [{"citedPaper": None}, None]}, "end_reported_by_api"),
    ({"data": None}, "error"), ({}, "error"),
])
def test_null_empty_and_pagination(payload, expected):
    if expected == "error":
        with pytest.raises(ValueError):
            spike.summarize_relations(payload, "references", "seed", 5)
    else:
        summary = spike.summarize_relations(payload, "references", "seed", 5)
        assert summary["pagination"] == expected
        assert summary["unresolved"] == len(payload["data"])
        assert summary["empty_list"] == (payload["data"] == [])
    gap = spike.summarize_relations({"data": []}, "references", "seed", 5, 10)
    assert gap["pagination"] == "count_mismatch_without_next" and gap["truncated"] is None


@pytest.mark.parametrize("only", [False, True])
def test_cli_limits_no_key_references_only_save_and_redact(monkeypatch, tmp_path, data, capsys, only):
    for bad in ("0", "1001", "x", "-1"):
        with pytest.raises(SystemExit):
            spike.main(["ARXIV:0000.00000", "--references-limit", bad])
    for bad in ("", "a title", "ARXIV:"):
        with pytest.raises(SystemExit):
            spike.main([bad])
    assert spike.page_limit("1") == 1 and spike.page_limit("1000") == 1000
    assert spike.input_id("DOI:10.1234/example") == "DOI:10.1234/example"
    fixture = copy.deepcopy(data)
    fixture["metadata"]["title"] = "Echoed test-secret"
    fixture["references"]["data"] = [
        {"contexts": ["LONG_CONTEXT_" + "x" * 1000], "intents": [], "isInfluential": False,
         "citedPaper": {"paperId": f"ref-{i}", "title": f"Reference {i}", "year": 2020}}
        for i in range(20)
    ]
    fixture["citations"]["data"][0]["citingPaper"].update(title="Citing paper", year=2025)
    replies = [response(fixture[k]) for k in (("references",) if only else ("metadata", "references", "citations"))]
    client, get = fake_client(monkeypatch, replies, None if only else "test-secret")
    assert ("x-api-key" in client.session.headers) is not only
    monkeypatch.setattr(spike, "S2Client", lambda key: client)
    monkeypatch.setattr(spike, "OUTPUT_DIR", tmp_path / "raw")
    argv = ["ARXIV:0000.00000", "--references-limit", "1000"]
    if only:
        argv.append("--references-only")
    assert spike.main(argv) == 0
    saved = next((tmp_path / "raw").glob("*.json")).read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "test-secret" not in saved + output
    assert "LONG_CONTEXT_" not in output
    examples = [line for line in output.splitlines() if line.startswith("Example ")]
    assert len(examples) == 3
    assert '"title": "Reference 0", "year": 2020' in examples[0]
    assert all(" -> " in line for line in examples)
    if not only:
        assert '"title": "Citing paper", "year": 2025' in examples[1].split(" -> ")[0]
        assert '"paper_id": "seed-id"' in examples[1].split(" -> ")[1]
    else:
        assert '"title": null, "year": null' in examples[0].split(" -> ")[0]
    assert get.call_count == (1 if only else 3)
    report = json.loads(saved)
    assert report["results"]["references"]["params"]["limit"] == 1000
    assert "response" in report["results"]["references"]
    refs = report["results"]["references"]
    assert len(refs["response"]["data"]) == len(refs["summary"]["items"]) == 20
    item = refs["summary"]["items"][0]
    assert {"index", "paper_id", "edge", "contexts", "intents", "isInfluential", "field_states"} <= item.keys()
    assert item["title"] == "Reference 0" and item["year"] == 2020
    assert "LONG_CONTEXT_" in saved


@pytest.mark.parametrize("status,kind", [(400, "bad_request"), (401, "authentication"), (403, "forbidden"), (404, "not_found")])
def test_nonretryable_http_stops_metadata_dependencies(monkeypatch, status, kind):
    client, get = fake_client(monkeypatch, [response(status=status)])
    report = spike.run_probe(client, "ARXIV:0000.00000")
    assert get.call_count == 1
    assert report["results"]["metadata"]["error"]["kind"] == kind
    assert report["results"]["references"]["status"] == "skipped"
    assert report["status"] == "incomplete"


@pytest.mark.parametrize("key,status,attempts", [(None, 429, 2), ("test-secret", 429, 3), (None, 503, 3)])
def test_retry_budgets_and_retry_after(monkeypatch, offline, key, status, attempts):
    client, get = fake_client(monkeypatch, [response(status=status)] * 3, key)
    report = spike.run_probe(client, "ARXIV:0000.00000")
    assert get.call_count == attempts and report["http_attempts"] == attempts
    assert report["results"]["citations"]["status"] == "skipped"
    client, get = fake_client(monkeypatch, [response(status=429, headers={"Retry-After": "120"})], key)
    result = client.request_json("/paper/x", {"fields": "title"})
    assert get.call_count == 1 and "wait budget" in result["error"]["message"]
    assert spike.retry_delay("bad header", 1) == 2
    assert spike.retry_delay("3", 1) == 3
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    assert spike.retry_delay(future, 1) > 10
    client, get = fake_client(monkeypatch, [response(status=503), response({"paperId": "ok"})], key)
    assert client.request_json("/paper/x", {"fields": "paperId"})["status"] == "ok"
    assert get.call_count == 2


@pytest.mark.parametrize("exception,kind,attempts,recover", [
    (requests.exceptions.SSLError("test-secret"), "ssl", 1, False),
    (requests.exceptions.Timeout("test-secret"), "timeout", 3, False),
    (requests.exceptions.ConnectionError("test-secret"), "connection", 3, False),
    (requests.exceptions.SSLError(ssl.SSLEOFError(8, "test-secret")), "ssl", 3, False),
    (requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING test-secret"), "ssl", 2, True),
    (requests.exceptions.SSLError("Connection reset by peer test-secret"), "ssl", 2, True),
    (requests.exceptions.SSLError(ssl.SSLCertVerificationError(1, "test-secret")), "ssl", 1, False),
    (requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED; unexpected EOF test-secret"), "ssl", 1, False),
    (requests.exceptions.SSLError("hostname mismatch test-secret"), "ssl", 1, False),
])
def test_network_errors_are_bounded_and_safe(monkeypatch, offline, exception, kind, attempts, recover):
    replies = [exception, response({"paperId": "ok"})] if recover else [exception] * 3
    client, get = fake_client(monkeypatch, replies, "test-secret")
    starts = []
    events = iter(replies)
    def get_response(*args, **kwargs):
        starts.append(offline[0])
        assert kwargs.get("verify", client.session.verify) is True
        event = next(events)
        if isinstance(event, Exception):
            raise event
        return event
    get.side_effect = get_response
    result = client.request_json("/paper/x", {"fields": "title"})
    if recover:
        assert result["status"] == "ok" and "error" not in result
    else:
        assert result["error"]["kind"] == kind and result["stop_run"]
    assert get.call_count == attempts and "test-secret" not in json.dumps(result)
    assert all(b - a >= 1.1 for a, b in zip(starts, starts[1:]))
    assert all(event["error_kind"] == kind for event in result["attempts"][:attempts - int(recover)])


@pytest.mark.parametrize("mode", ["json", "body", "paper_id", "data"])
def test_bad_response_shapes_and_partial_success(monkeypatch, data, mode):
    bad = response([] if mode == "body" else {})
    if mode == "json":
        bad.json.side_effect = ValueError("unsafe remote text")
    if mode == "data":
        client, get = fake_client(monkeypatch, [response(data["metadata"]), response({"data": None}), response(data["citations"])])
    else:
        client, get = fake_client(monkeypatch, [bad])
    report = spike.run_probe(client, "ARXIV:0000.00000")
    assert report["status"] == "incomplete"
    assert get.call_count == (3 if mode == "data" else 1)
    if mode == "data":
        assert report["results"]["citations"]["status"] == "ok"
        assert report["results"]["references"]["error"]["kind"] == "invalid_shape"


def test_output_write_failure(monkeypatch, tmp_path, data, capsys):
    client, _ = fake_client(monkeypatch, [response(data["references"])])
    monkeypatch.setattr(spike, "S2Client", lambda key: client)
    blocked = tmp_path / "file-not-directory"
    blocked.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(spike, "OUTPUT_DIR", blocked)
    assert spike.main(["ARXIV:0000.00000", "--references-only"]) == 1
    assert "save failed" in capsys.readouterr().out
