"""Extraction indépendante : un PDF téléchargé reste disponible même si l'OCR échoue."""

import io
import logging
import time
from datetime import timedelta
from uuid import uuid4
from bs4 import BeautifulSoup
from docx import Document as WordDocument
from pypdf import PdfReader
from sqlalchemy import select, update
from .config import settings
from .db import transaction
from .models import Version, now
from .repository import emit
from .storage import resolve, store_text

log = logging.getLogger(__name__)


def extract_basic(data, kind):
    pages = []
    if kind == "pdf":
        for number, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1):
            pages.append({"page": number, "text": page.extract_text() or ""})
    elif kind == "docx":
        doc = WordDocument(io.BytesIO(data))
        text = "\n".join(
            [p.text for p in doc.paragraphs]
            + [
                " | ".join(c.text for c in row.cells)
                for t in doc.tables
                for row in t.rows
            ]
        )
        pages = [{"page": None, "text": text}]
    elif kind == "html":
        soup = BeautifulSoup(data, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        pages = [{"page": None, "text": main.get_text("\n", strip=True)}]
    elif kind in ("txt", "csv", "rtf"):
        pages = [{"page": None, "text": data.decode("utf-8-sig", errors="replace")}]
    else:
        return {"text": "", "pages": [], "engine": "basic"}, "unsupported"
    text = "\n\n".join(p["text"] for p in pages)
    status = (
        "needs_ocr"
        if kind == "pdf" and any(not p["text"].strip() for p in pages)
        else ("done" if text.strip() else "empty")
    )
    return {"text": text, "pages": pages, "engine": "basic"}, status


def convert(version, kind):
    if settings.extractor == "docling" and kind in (
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "html",
    ):
        from docling.document_converter import DocumentConverter

        # Extension nécessaire pour identifier le format du blob sans extension.
        path = resolve(version.export_path)
        result = DocumentConverter().convert(path)
        document = result.document
        text = document.export_to_markdown()
        return {
            "text": text,
            "document": document.export_to_dict(),
            "engine": "docling",
        }, "done" if text.strip() else "empty"
    return extract_basic(resolve(version.blob_path).read_bytes(), kind)


def claim():
    token = str(uuid4())
    with transaction() as db:
        # L'extracteur doit être lancé en un exemplaire ; les tâches abandonnées restent reprenables.
        db.execute(
            update(Version)
            .where(
                Version.extraction_status == "processing",
                Version.extraction_started < now() - timedelta(hours=1),
            )
            .values(extraction_status="pending", extraction_owner=None)
        )
        version = db.scalar(
            select(Version)
            .where(Version.extraction_status == "pending")
            .order_by(Version.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not version:
            return None
        version.extraction_status = "processing"
        version.extraction_owner = token
        version.extraction_started = now()
        return version, version.kind, token


def process_one():
    claimed = claim()
    if not claimed:
        return False
    version, kind, token = claimed
    status, error, path = "failed", "", ""
    try:
        payload, status = convert(version, kind)
        path = store_text(version.id, payload)
    except Exception as exc:
        log.exception("Extraction impossible : %s", version.id)
        error = str(exc)[:2000]
    with transaction() as db:
        current = db.scalar(
            select(Version).where(Version.id == version.id).with_for_update()
        )
        if current.extraction_owner != token:
            return True
        current.extraction_status = status
        current.extraction_error = error
        current.text_path = path
        current.extraction_owner = None
        emit(db, current.document_id, current.id, "document.extracted")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            if not process_one():
                time.sleep(2)
        except Exception:
            log.exception("Erreur extracteur ; nouvelle tentative dans 5 s")
            time.sleep(5)
