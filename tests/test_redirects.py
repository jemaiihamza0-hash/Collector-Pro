import io
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.db import transaction
from app.models import Document, Task
from app.repository import claim_job


def test_http_redirects_are_followed_in_scope_and_refused_out_of_scope(
    authorized, monkeypatch
):
    """Real sites redirect constantly (http->https, www, download links).

    With every status handled by the spider, Scrapy's RedirectMiddleware never
    ran, so a 302 ended as an empty response. An in-scope redirect must now be
    followed and recorded with its final URL; a redirect leaving the source's
    hosts must still be refused by the scope policy, even though the target
    here is the same fixture server reachable under another host name.
    """
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    pdf = io.BytesIO()
    writer.write(pdf)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            port = self.server.server_port
            if self.path == "/":
                body = (
                    b'<html><title>Fixture</title>'
                    b'<a href="/old.pdf">Ancien lien</a>'
                    b'<a href="/escape.pdf">Hors perimetre</a></html>'
                )
                mime = "text/html"
            elif self.path == "/old.pdf":
                self.send_response(302)
                self.send_header("Location", "/rapport.pdf")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            elif self.path == "/escape.pdf":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}/rapport.pdf")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            elif self.path == "/rapport.pdf":
                body = pdf.getvalue()
                mime = "application/pdf"
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
                "mode": "http",
                "max_urls": 20,
            },
        )
        assert result.status_code == 201, result.text
        ident = result.json()["job_id"]
        assert claim_job("redirect-owner", 180) == ident
        child = subprocess.run(
            [sys.executable, "-m", "tests.crawl_fixture", ident, "redirect-owner"],
            env={
                **os.environ,
                "CRAWL_DELAY": "0.01",
                "CRAWL_CONCURRENCY": "2",
                "FIXTURE_PORT": str(port),
            },
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert child.returncode == 0, child.stderr[-5000:]
        with transaction() as db:
            pdfs = db.scalars(select(Document).where(Document.kind == "pdf")).all()
            assert len(pdfs) == 1, [(d.url, d.final_url) for d in pdfs]
            assert pdfs[0].url.endswith("/old.pdf")
            assert pdfs[0].final_url.endswith("/rapport.pdf")
            assert "localhost" not in pdfs[0].final_url
            failed = db.scalars(select(Task).where(Task.status == "failed")).all()
            assert [t.url for t in failed if t.url.endswith("/escape.pdf")], [
                (t.url, t.error) for t in failed
            ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
