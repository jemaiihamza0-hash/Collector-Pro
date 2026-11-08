import io
import json
import zipfile
from datetime import timedelta
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from docx import Document as Word
from sqlalchemy import select
from app.config import settings
from app.db import transaction
from app.models import Job, Task, Version, Change, now
from app.repository import (
    claim_job,
    claim_tasks,
    enqueue,
    record_document,
    record_failure,
    LostLease,
)
from app.storage import classify, store_original
from app.extract import process_one
from app.urls import normalize, public_address, PolicyError
import pytest


def make_job(client, **kwargs):
    result = client.post(
        "/api/sources",
        json={"url": "https://example.com/", "respect_robots": False, **kwargs},
    )
    assert result.status_code == 201, result.text
    return result.json()


def prepare_document(client, body=b"Bonjour", kind="txt"):
    job = make_job(client)
    owner = "owner-one"
    assert claim_job(owner, 180) == job["job_id"]
    task = claim_tasks(job["job_id"], owner, 1)[0]
    url = "https://example.com/rapport." + kind
    stored = store_original(body, job["source_id"], url, kind, "rapport." + kind)
    doc = record_document(
        job["job_id"],
        owner,
        task["id"],
        url,
        "Rapport",
        kind,
        "text/plain",
        body,
        stored,
        {"_status": 200},
        [],
    )
    return job, doc


def test_api_requires_key(client):
    assert client.get("/api/documents").status_code == 401
    assert client.post("/api/login", json={"key": "bad"}).status_code == 401
    assert client.post("/api/login", json={"key": settings.api_key}).status_code == 200
    assert client.get("/api/documents").status_code == 200


def test_policy_snapshot_and_duplicate_active_source(authorized):
    result = make_job(authorized)
    assert authorized.get("/api/jobs").json()[0]["policy"]["respect_robots"] is False
    assert (
        authorized.post("/api/sources", json={"url": "https://example.com"}).status_code
        == 409
    )
    assert (
        authorized.post("/api/jobs/" + result["job_id"] + "/pause").json()["status"]
        == "paused"
    )
    assert (
        authorized.post("/api/jobs/" + result["job_id"] + "/resume").json()["status"]
        == "queued"
    )


def test_real_private_ip_rejected():
    with pytest.raises(PolicyError):
        public_address("http://127.0.0.1/")
    with pytest.raises(PolicyError):
        normalize("http://user:password@example.com/")
    assert (
        normalize("https://example.com/a?x=1&utm_source=a#part")
        == "https://example.com/a?x=1"
    )


def test_pdf_signature_and_parse_are_required():
    with pytest.raises(ValueError):
        classify(b"<html>Forbidden</html>", "text/html", "https://example.com/a.pdf")
    with pytest.raises(Exception):
        classify(b"%PDF-broken", "application/pdf", "https://example.com/a.pdf")
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    out = io.BytesIO()
    writer.write(out)
    assert (
        classify(out.getvalue(), "application/pdf", "https://example.com/download")[0]
        == "pdf"
    )


def test_invalid_office_not_download_success():
    with pytest.raises(Exception):
        classify(b"error", "application/octet-stream", "https://example.com/a.docx")


def test_download_file_and_text_api_and_export(authorized):
    job, doc = prepare_document(authorized)
    assert authorized.get(f"/api/documents/{doc}/file").content == b"Bonjour"
    assert authorized.get(f"/api/documents/{doc}/text").status_code == 409
    assert process_one()
    text = authorized.get(f"/api/documents/{doc}/text").json()
    assert text["text"] == "Bonjour" and text["status"] == "done"
    payload = authorized.get("/api/sources/" + job["source_id"] + "/export").content
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "catalogue.json" in archive.namelist()
        assert any("/documents/rapport__" in name for name in archive.namelist())


def test_export_disambiguates_text_for_pdfs_with_the_same_filename(authorized):
    job = make_job(authorized)
    owner = "export-test"
    assert claim_job(owner, 180) == job["job_id"]
    expected = {
        "https://example.com/first/report.pdf": "Text from the first report",
        "https://example.com/second/report.pdf": "Text from the second report",
    }
    with transaction() as db:
        enqueue(db, db.get(Job, job["job_id"]), list(expected))
    tasks = {task["url"]: task["id"] for task in claim_tasks(job["job_id"], owner, 10)}

    for url, text in expected.items():
        writer = PdfWriter()
        page = writer.add_blank_page(200, 200)
        name = NameObject
        page[name("/Resources")] = DictionaryObject({
            name("/Font"): DictionaryObject({
                name("/F1"): DictionaryObject({
                    name("/Type"): name("/Font"),
                    name("/Subtype"): name("/Type1"),
                    name("/BaseFont"): name("/Helvetica"),
                }),
            }),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("ascii"))
        page[name("/Contents")] = stream
        output = io.BytesIO()
        writer.write(output)
        body = output.getvalue()
        stored = store_original(body, job["source_id"], url, "pdf", "report.pdf")
        record_document(
            job["job_id"], owner, tasks[url], url, "report.pdf", "pdf",
            "application/pdf", body, stored, {}, [],
        )
        assert process_one()

    response = authorized.get(f'/api/sources/{job["source_id"]}/export')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        text_paths = [name for name in archive.namelist() if name.endswith(".txt")]
        assert sorted(text_paths) == ["text/report-2.txt", "text/report.txt"]
        assert {archive.read(path).decode("utf-8") for path in text_paths} == set(
            expected.values()
        )
        catalogue = json.loads(archive.read("catalogue.json"))
        assert {entry["url"] for entry in catalogue} == set(expected)
        assert sorted(entry["txt_path"] for entry in catalogue) == sorted(text_paths)
        for entry in catalogue:
            pdf = PdfReader(io.BytesIO(archive.read(entry["pdf_path"])))
            text = archive.read(entry["txt_path"]).decode("utf-8")
            assert pdf.pages[0].extract_text() == text == expected[entry["url"]]


def test_versions_and_change_feed_are_idempotent(authorized):
    job, doc = prepare_document(authorized)
    with transaction() as db:
        task_id = db.scalar(select(Task).where(Task.document_id == doc)).id
    stored = store_original(
        b"Bonjour", job["source_id"], "https://example.com/a.txt", "txt", "a.txt"
    )
    record_document(
        job["job_id"],
        "owner-one",
        task_id,
        "https://example.com/a.txt",
        "Rapport",
        "txt",
        "text/plain",
        b"Bonjour",
        stored,
        {},
        [],
    )
    with transaction() as db:
        assert len(db.scalars(select(Version)).all()) == 1
        assert len(db.scalars(select(Change)).all()) == 1
    assert process_one()
    feed = authorized.get("/api/changes?cursor=0").json()
    assert [e["event"] for e in feed["items"]] == [
        "document.updated",
        "document.extracted",
    ]
    assert (
        authorized.get("/api/changes?cursor=" + str(feed["next_cursor"])).json()[
            "items"
        ]
        == []
    )


def test_stale_owner_cannot_commit_and_pending_tasks_recover(authorized):
    job = make_job(authorized)
    assert claim_job("old", 180) == job["job_id"]
    claim_tasks(job["job_id"], "old", 1)
    with transaction() as db:
        db.get(Job, job["job_id"]).heartbeat = now() - timedelta(seconds=200)
    assert claim_job("new", 180) == job["job_id"]
    with pytest.raises(LostLease):
        claim_tasks(job["job_id"], "old", 1)
    assert claim_tasks(job["job_id"], "new", 1)


def test_retry_is_bounded(authorized):
    job = make_job(authorized)
    claim_job("owner", 180)
    for number in range(3):
        task = claim_tasks(job["job_id"], "owner", 1)[0]
        record_failure(job["job_id"], "owner", task["id"], "HTTP 503", 503, True)
        with transaction() as db:
            db.get(Task, task["id"]).ready_at = now() - timedelta(seconds=1)
    with transaction() as db:
        assert db.get(Task, task["id"]).status == "failed"
        assert db.get(Task, task["id"]).attempts == 3


def test_docx_unicode_and_tables(authorized):
    word = Word()
    word.add_paragraph("Résultat — النتيجة")
    table = word.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Chiffre"
    table.cell(0, 1).text = "125"
    out = io.BytesIO()
    word.save(out)
    _, doc = prepare_document(authorized, out.getvalue(), "docx")
    assert process_one()
    text = authorized.get(f"/api/documents/{doc}/text").json()["text"]
    assert "النتيجة" in text and "125" in text


def test_robots_disabled_does_not_fetch_rules(monkeypatch):
    from app.urls import Policy

    monkeypatch.setattr("app.urls.public_address", lambda url: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("robots.txt ne doit pas être consulté")

    monkeypatch.setattr("app.urls.httpx.Client", unexpected)
    assert Policy(["example.com"], False).check("https://example.com/private") == 0


def test_robots_enabled_excludes_path_and_is_cached(monkeypatch):
    from app.urls import Policy
    import httpx
    from unittest.mock import MagicMock

    monkeypatch.setattr("app.urls.public_address", lambda url: None)
    response = httpx.Response(200, content=b"User-agent: *\nDisallow: /private\n")
    manager = MagicMock()
    manager.__enter__.return_value.stream.return_value.__enter__.return_value = response
    client = MagicMock(return_value=manager)
    monkeypatch.setattr("app.urls.httpx.Client", client)
    policy = Policy(["example.com"], True)
    with pytest.raises(PolicyError):
        policy.check("https://example.com/private")
    assert policy.check("https://example.com/public") == 0
    client.assert_called_once()


def test_browser_navigation_applies_same_robots_policy(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.spider import CollectorSpider

    monkeypatch.setattr("app.spider.public_address", lambda url: None)
    policy = MagicMock()
    policy.check.side_effect = PolicyError("robots")
    request = SimpleNamespace(
        url="https://example.com/private",
        resource_type="document",
        is_navigation_request=lambda: True,
    )
    assert (
        asyncio.run(
            CollectorSpider.abort_browser(SimpleNamespace(policy=policy), request)
        )
        is True
    )
    policy.check.assert_called_once_with(request.url)


def test_cmf_adapter_discovers_pagination_and_get_year_filters():
    from app.adapters import html_links

    body = b'<form method="get"><select name="year"><option value="">Tous</option><option value="2025">2025</option><option value="2026">2026</option></select></form><a href="?q=bulletin-officiel&page=1">Suivant</a>'
    _, links = html_links(body, "https://www.cmf.tn/?q=bulletin-officiel")
    assert any("year=2025" in link for link in links)
    assert any("page=1" in link for link in links)


def test_fresh_version_keeps_old_original_accessible(authorized):
    job, doc = prepare_document(authorized)
    first = authorized.get(f"/api/documents/{doc}").json()["version_id"]
    with transaction() as db:
        task_id = db.scalar(select(Task).where(Task.document_id == doc)).id
    stored = store_original(
        b"Nouvelle version",
        job["source_id"],
        "https://example.com/rapport.txt",
        "txt",
        "rapport.txt",
    )
    record_document(
        job["job_id"],
        "owner-one",
        task_id,
        "https://example.com/rapport.txt",
        "Rapport",
        "txt",
        "text/plain",
        b"Nouvelle version",
        stored,
        {},
        [],
    )
    assert len(authorized.get(f"/api/documents/{doc}/versions").json()) == 2
    assert (
        authorized.get(f"/api/documents/{doc}/file?version_id={first}").content
        == b"Bonjour"
    )
    assert authorized.get(f"/api/documents/{doc}/file").content == b"Nouvelle version"


def test_office_mime_works_without_extension():
    word = Word()
    word.add_paragraph("Document sans extension")
    output = io.BytesIO()
    word.save(output)
    kind, _ = classify(
        output.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "https://example.com/download?id=1",
    )
    assert kind == "docx"


def test_foreign_origin_cannot_control_jobs(authorized):
    response = authorized.post(
        "/api/sources",
        json={"url": "https://example.com"},
        headers={"Origin": "https://other.example"},
    )
    assert response.status_code == 403


def test_postgresql_schema_and_locking_sql_compile():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    from app.models import Base

    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=postgresql.dialect()))
    sql = str(
        select(Task)
        .with_for_update(skip_locked=True)
        .compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_periodic_schedule_and_read_key(authorized, monkeypatch):
    from dataclasses import replace
    from datetime import timedelta

    from app import api
    from app.db import transaction
    from app.models import Job, Schedule, now
    from app.repository import enqueue_due_source
    from sqlalchemy import select

    response = authorized.post(
        "/api/sources",
        json={
            "url": "https://example.com/",
            "interval_minutes": 60,
            "respect_robots": False,
            "mode": "http",
            "max_urls": 25,
        },
    )
    assert response.status_code == 201
    ids = response.json()
    source_id = ids["source_id"]

    with transaction() as db:
        db.get(Schedule, source_id).next_run = now() - timedelta(days=3)
    assert not enqueue_due_source()
    with transaction() as db:
        db.get(Job, ids["job_id"]).status = "paused"
    assert not enqueue_due_source()
    with transaction() as db:
        db.get(Job, ids["job_id"]).status = "completed"
    assert enqueue_due_source()
    assert not enqueue_due_source()
    with transaction() as db:
        new_job = db.scalar(select(Job).where(Job.id != ids["job_id"]))
        assert new_job.policy["max_urls"] == 25
        assert new_job.policy["mode"] == "http"
        assert new_job.policy["respect_robots"] is False
        assert db.scalar(select(Schedule.source_id).where(Schedule.next_run > now()))

    url = f"/api/sources/{source_id}/schedule"
    assert (
        authorized.patch(url, json={"interval_minutes": 120}).json()["interval_minutes"]
        == 120
    )
    assert (
        authorized.patch(url, json={"interval_minutes": 0}).json()["next_run"] is None
    )
    assert not enqueue_due_source()

    reader_key = "test-reader-key-only-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(
        api, "settings", replace(api.settings, reader_api_key=reader_key)
    )
    reader = {"X-API-Key": reader_key}
    assert authorized.get("/api/documents", headers=reader).status_code == 200
    assert authorized.get("/api/changes", headers=reader).status_code == 200
    assert authorized.get("/api/jobs", headers=reader).status_code == 401
    assert (
        authorized.patch(url, headers=reader, json={"interval_minutes": 1}).status_code
        == 401
    )
    assert authorized.post("/api/login", json={"key": reader_key}).status_code == 401
