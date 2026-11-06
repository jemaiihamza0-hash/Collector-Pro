import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from pypdf import PdfReader
from .config import settings

DOCUMENT_EXT = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".odt",
    ".zip",
    ".csv",
    ".txt",
    ".rtf",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(value):
    name = (
        re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", unquote(value)).strip(" .")[:120]
        or "document"
    )
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def resolve(relative):
    path = (settings.storage / relative).resolve()
    if not path.is_relative_to(settings.storage.resolve()):
        raise ValueError("Chemin de stockage invalide.")
    return path


def atomic_write(relative, body):
    path = resolve(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return relative


def classify(body, mime, url, suggested=""):
    if not body:
        raise ValueError("Réponse vide.")
    if len(body) > settings.max_bytes:
        raise ValueError("Fichier supérieur à la taille autorisée.")
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suggested:
        suffix = Path(suggested).suffix.lower() or suffix
    mime = mime.split(";")[0].strip().lower()
    mime_extensions = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/msword": ".doc",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.ms-powerpoint": ".ppt",
    }
    if not suffix or suffix not in DOCUMENT_EXT:
        suffix = mime_extensions.get(mime, suffix)
    head = body.lstrip()[:100].lower()
    if body.startswith(b"%PDF-") or mime == "application/pdf" or suffix == ".pdf":
        if not body.startswith(b"%PDF-"):
            raise ValueError("Le serveur a renvoyé autre chose qu’un PDF.")
        try:
            reader = PdfReader(io.BytesIO(body))
            if not reader.is_encrypted and len(reader.pages) == 0:
                raise ValueError("PDF sans pages.")
        except Exception as exc:
            raise ValueError("PDF invalide ou tronqué.") from exc
        return "pdf", "application/pdf"
    if suffix in (".docx", ".xlsx", ".pptx", ".odt"):
        expected = {
            ".docx": "word/document.xml",
            ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml",
            ".odt": "content.xml",
        }[suffix]
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            if (
                len(z.infolist()) > 5000
                or sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024
            ):
                raise ValueError(
                    "Document Office trop complexe ou décompressé supérieur à 200 Mo."
                )
            if expected not in z.namelist():
                raise ValueError("Document Office/ODF invalide.")
        return suffix[1:], mime or "application/zip"
    if "html" in mime or head.startswith((b"<!doctype html", b"<html")):
        if suffix in DOCUMENT_EXT:
            raise ValueError("Page HTML reçue à la place du document demandé.")
        return "html", "text/html"
    if "xml" in mime or suffix in (".xml", ".rss", ".gz") or head.startswith(b"<?xml"):
        return "index", mime or "application/xml"
    if suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            z.infolist()
        return "zip", "application/zip"
    if suffix in (".doc", ".xls", ".ppt"):
        if not body.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise ValueError("Ancien document Office invalide.")
        return suffix[1:], mime or "application/octet-stream"
    if (
        suffix in (".txt", ".csv", ".rtf")
        or mime.startswith("text/plain")
        or mime == "text/csv"
    ):
        return (suffix[1:] if suffix else "txt"), mime or "text/plain"
    raise ValueError("Format non pris en charge : " + (mime or suffix or "inconnu"))


def store_original(body, source_id, url, kind, suggested=""):
    digest = sha(body)
    blob = atomic_write(f"blobs/{digest[:2]}/{digest}", body)
    name = safe_name(suggested or Path(urlsplit(url).path).name or "page")
    stem = Path(name).stem
    directory = "pages" if kind == "html" else "documents"
    export = f"sources/{source_id}/{directory}/{stem}__{digest[:12]}.{kind}"
    target = resolve(export)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            os.link(resolve(blob), target)
        except OSError:
            atomic_write(export, body)
    return digest, blob, export


def store_text(version_id, payload):
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return atomic_write(f"text/{version_id}.json", body)
