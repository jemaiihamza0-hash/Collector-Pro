import io
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pypdf import PdfWriter
from docx import Document as Word
from sqlalchemy import select
from app.db import transaction
from app.models import Job, Task, Document
from app.repository import claim_job
from app.extract import process_one
import pytest


@pytest.mark.parametrize("mode", ["http", "browser"])
def test_real_scrapy_http_download_database_and_extraction(
    authorized, monkeypatch, mode
):
    if mode == "browser":
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                pytest.skip(
                    "Chromium absent : exécuter python -m playwright install chromium"
                )
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    pdf = io.BytesIO()
    writer.write(pdf)
    word = Word()
    word.add_paragraph("Rapport de test réellement téléchargé")
    docx = io.BytesIO()
    word.save(docx)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/":
                body = b'<html><title>Fixture</title><p>Collection</p><a href="/rapport.pdf">PDF</a><a href="/page2">Suite</a><a href="/faux.pdf">Invalide</a></html>'
                mime = "text/html"
            elif self.path == "/page2":
                body = b'<html><title>Deux</title><a href="/rapport.docx">Word</a><a href="/">Accueil</a></html>'
                mime = "text/html"
            elif self.path == "/rapport.pdf":
                body = pdf.getvalue()
                mime = "application/pdf"
            elif self.path == "/rapport.docx":
                body = docx.getvalue()
                mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif self.path == "/faux.pdf":
                body = b"<html>Access denied</html>"
                mime = "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as exc:
        pytest.skip(f"Port de fixture indisponible : {exc}")
    from tests.fixture_urls import fixture_normalizer

    port = server.server_port
    normalizer = fixture_normalizer(port)
    monkeypatch.setattr("app.api.normalize", normalizer)
    monkeypatch.setattr("app.urls.normalize", normalizer)
    monkeypatch.setattr("app.repository.normalize", normalizer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = authorized.post(
            "/api/sources",
            json={
                "url": f"http://127.0.0.1:{port}/",
                "respect_robots": False,
                "mode": mode,
                "max_urls": 100,
            },
        )
        assert result.status_code == 201, result.text
        ident = result.json()["job_id"]
        assert claim_job("integration-owner", 180) == ident
        environment = {
            **os.environ,
            "CRAWL_DELAY": "0.01",
            "CRAWL_CONCURRENCY": "2",
            "FIXTURE_PORT": str(port),
        }
        child = subprocess.run(
            [sys.executable, "-m", "tests.crawl_fixture", ident, "integration-owner"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert child.returncode == 0, child.stderr[-5000:]
        with transaction() as db:
            job = db.get(Job, ident)
            assert job.status == "partial", child.stderr[-5000:]
            docs = db.scalars(select(Document)).all()
            assert {d.kind for d in docs} == {"pdf", "docx", "html"}
            tasks = db.scalars(select(Task).where(Task.status == "failed")).all()
            assert len(tasks) == 1 and tasks[0].url.endswith("/faux.pdf"), [
                (t.url, t.error) for t in tasks
            ]
            assert "PDF" in tasks[0].error
        while process_one():
            pass
        docs = authorized.get("/api/documents").json()["items"]
        word_doc = next(d for d in docs if d["kind"] == "docx")
        assert (
            "réellement téléchargé"
            in authorized.get(word_doc["text_url"]).json()["text"]
        )
        pdf_doc = next(d for d in docs if d["kind"] == "pdf")
        assert authorized.get(pdf_doc["file_url"]).content == pdf.getvalue()
        assert pdf_doc["extraction_status"] == "needs_ocr"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
