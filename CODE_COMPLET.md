# Collecteur documentaire — code complet

Chaque section correspond à un fichier du ZIP. Créer les dossiers indiqués et copier le contenu du bloc dans le fichier correspondant. Le ZIP contient déjà tous les fichiers au bon emplacement.

## Lancement

Docker Desktop doit être démarré. Dans le dossier `collector_pro` :

```powershell
python scripts/setup.py
docker compose up --build -d
```

Ouvrir http://localhost:8000 puis saisir la valeur API_KEY du fichier `.env` créé localement.

## Architecture

Interface → FastAPI → PostgreSQL. Un worker Scrapy/Playwright télécharge les originaux ; un second worker extrait le texte. Les documents sont conservés dans le volume partagé. Le futur RAG communique uniquement avec l’API. Voir ARCHITECTURE.md et README.md pour les détails.

## Liste des fichiers

- `.dockerignore`
- `.env.example`
- `.github/workflows/tests.yml`
- `.gitignore`
- `Dockerfile`
- `app/__init__.py`
- `app/adapters/__init__.py`
- `app/adapters/cmf.py`
- `app/api.py`
- `app/config.py`
- `app/crawl.py`
- `app/db.py`
- `app/extract.py`
- `app/init_db.py`
- `app/models.py`
- `app/repository.py`
- `app/spider.py`
- `app/static/app.js`
- `app/static/index.html`
- `app/static/style.css`
- `app/storage.py`
- `app/urls.py`
- `app/worker.py`
- `compose.yaml`
- `requirements-dev.txt`
- `requirements-docling.txt`
- `requirements.txt`
- `scripts/setup.py`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/crawl_fixture.py`
- `tests/fixture_urls.py`
- `tests/test_http_integration.py`
- `tests/test_system.py`

## `.dockerignore`

```text
.env
.venv
__pycache__
.pytest_cache
storage
*.db
```

## `.env.example`

```text
# Générer .env avec : python scripts/setup.py
API_KEY=
POSTGRES_PASSWORD=
COOKIE_SECURE=false
MAX_FILE_BYTES=52428800
CRAWL_DELAY=1
CRAWL_CONCURRENCY=2
LEASE_SECONDS=180
INSTALL_DOCLING=false
EXTRACTOR=basic
```

## `.github/workflows/tests.yml`

```yaml
name: Tests
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17-bookworm
        env:
          POSTGRES_USER: collector
          POSTGRES_PASSWORD: ci_password
          POSTGRES_DB: collector_test
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U collector -d collector_test"
          --health-interval 5s --health-retries 20
    env:
      TEST_DATABASE_URL: postgresql+psycopg://collector:ci_password@localhost:5432/collector_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pip install -r requirements-dev.txt
      - run: python -m playwright install --with-deps chromium
      - run: python -m pytest -q
      - run: node --check app/static/app.js
```

## `.gitignore`

```text
.env
.venv/
__pycache__/
.pytest_cache/
storage/
*.db
*.db-shm
*.db-wal
```

## `Dockerfile`

```text
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /opt/collector
COPY requirements.txt requirements-docling.txt ./
ARG INSTALL_DOCLING=false
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DOCLING" = "true" ]; then pip install --no-cache-dir -r requirements-docling.txt; fi \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 collector \
    && mkdir -p /storage && chown collector:collector /storage
COPY --chown=collector:collector app ./app
USER collector
ENV STORAGE_DIR=/storage
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

## `app/__init__.py`

```python
"""Collecteur documentaire indépendant du RAG."""
```

## `app/adapters/__init__.py`

```python
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from .cmf import cmf_seeds, year_filter_links


def seeds(url):
    p = urlsplit(url)
    origin = f"{p.scheme}://{p.netloc}"
    result = [
        url,
        origin + "/sitemap.xml",
        origin + "/sitemap_index.xml",
        origin + "/rss.xml",
    ]
    if p.hostname.removeprefix("www.") == "cmf.tn":
        result.extend(cmf_seeds(origin))
    return list(dict.fromkeys(result))


def html_links(body, url):
    soup = BeautifulSoup(body, "html.parser")
    base = soup.find("base", href=True)
    base_url = urljoin(url, base["href"]) if base else url
    links = [
        urljoin(base_url, tag.get("href") or tag.get("src") or tag.get("data"))
        for tag in soup.select(
            'a[href],iframe[src],embed[src],object[data],link[rel="next"],link[rel="alternate"]'
        )
        if tag.get("href") or tag.get("src") or tag.get("data")
    ]
    if urlsplit(url).hostname.removeprefix("www.") == "cmf.tn":
        links.extend(year_filter_links(soup, url))
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    # Le plan CMF fournit les rubriques ; leurs liens de pagination sont suivis.
    return title, list(dict.fromkeys(links))


def xml_links(body, url):
    root = ElementTree.fromstring(body)
    links = []
    for tag in root.iter():
        name = tag.tag.rsplit("}", 1)[-1].lower()
        value = (tag.text or "").strip()
        if name == "loc" and value:
            links.append(urljoin(url, value))
        elif name in ("link", "enclosure"):
            href = tag.get("href") or tag.get("url") or value
            if href:
                links.append(urljoin(url, href))
    return list(dict.fromkeys(links))
```

## `app/adapters/cmf.py`

```python
"""Points d'entrée CMF vérifiés ; pas de génération de noms de PDF supposés."""


def cmf_seeds(origin):
    return [
        origin + "/?q=sitemap",
        origin + "/?q=bulletin-officiel",
        origin + "/?q=consultation-des-tats-financier-des-soci-t-s-faisant-ape",
    ]


def year_filter_links(soup, page_url):
    """Développe les filtres annuels GET exposés, sans combinaisons mois/jour."""
    import re
    from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

    links = []
    for form in soup.find_all("form"):
        if form.get("method", "get").lower() != "get":
            continue
        target = urlsplit(urljoin(page_url, form.get("action") or page_url))
        defaults = dict(parse_qsl(urlsplit(page_url).query, keep_blank_values=True))
        defaults.update(parse_qsl(target.query, keep_blank_values=True))
        for element in form.select("input[name]"):
            name = element["name"]
            if (
                element.get("type") == "hidden"
                and "token" not in name
                and "build_id" not in name
            ):
                defaults[name] = element.get("value", "")
        for select in form.select("select[name]"):
            chosen = select.find("option", selected=True) or select.find("option")
            if chosen:
                defaults[select["name"]] = chosen.get("value", "")
        for select in form.select("select[name]"):
            values = [o.get("value", "") for o in select.find_all("option")]
            years = [v for v in values if re.fullmatch(r"(19|20)\d{2}", v)]
            if not years:
                continue
            for year in years:
                query = {**defaults, select["name"]: year}
                query.pop("page", None)
                links.append(
                    urlunsplit(
                        (
                            target.scheme,
                            target.netloc,
                            target.path,
                            urlencode(query),
                            "",
                        )
                    )
                )
    return links
```

## `app/api.py`

```python
import csv
import io
import json
import secrets
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update, text as sql_text
from sqlalchemy.exc import IntegrityError
from starlette.background import BackgroundTask
from .config import settings
from .db import transaction
from .models import Source, Job, Task, Document, Version, Change, now, uid
from .repository import enqueue
from .urls import normalize, allowed_hosts, public_address
from .storage import sha, resolve, safe_name
from .adapters import seeds


@asynccontextmanager
async def lifespan(app):
    if len(settings.api_key) < 24:
        raise RuntimeError("Configurer une API_KEY aléatoire de 24 caractères minimum.")
    yield


app = FastAPI(title="Document Collector", version="1.0.0", lifespan=lifespan)


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse(
        {
            "detail": "Ajout concurrent ou ressource déjà présente ; actualiser puis réessayer."
        },
        status_code=409,
    )


@app.middleware("http")
async def same_origin(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        if origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Origine non autorisée"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def authenticate(request: Request):
    key = request.headers.get("X-API-Key") or request.cookies.get(
        "collector_session", ""
    )
    if not key or not secrets.compare_digest(key.encode(), settings.api_key.encode()):
        raise HTTPException(401, "Clé API invalide.")


class Login(BaseModel):
    key: str


@app.post("/api/login")
def login(body: Login, response: Response):
    if not secrets.compare_digest(body.key.encode(), settings.api_key.encode()):
        raise HTTPException(401, "Clé invalide.")
    response.set_cookie(
        "collector_session",
        body.key,
        httponly=True,
        samesite="strict",
        secure=settings.secure_cookie,
        max_age=28800,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("collector_session")
    return {"ok": True}


class CreateSource(BaseModel):
    url: str = Field(max_length=4096)
    respect_robots: bool = True
    extra_hosts: list[str] = Field(default_factory=list, max_length=20)
    max_urls: int = Field(default=1000, ge=0, le=1000000)
    mode: str = "auto"


def create_job(db, source, body):
    if db.scalar(
        select(Job).where(
            Job.source_id == source.id, Job.status.in_(["queued", "running", "pausing"])
        )
    ):
        raise HTTPException(409, "Ce site est déjà en cours ou en attente.")
    job = Job(
        id=uid(),
        source_id=source.id,
        policy={
            "hosts": source.hosts,
            "respect_robots": body.respect_robots,
            "max_urls": body.max_urls,
            "mode": body.mode,
            "limited": False,
        },
    )
    db.add(job)
    db.flush()
    enqueue(db, job, seeds(source.url))
    return job


@app.post("/api/sources", dependencies=[Depends(authenticate)], status_code=201)
def add_source(body: CreateSource):
    if body.mode not in ("auto", "http", "browser"):
        raise HTTPException(422, "Mode invalide.")
    try:
        url = normalize(body.url)
        public_address(url)
        hosts = allowed_hosts(url, body.extra_hosts)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    with transaction() as db:
        # Verrou de la source pour sérialiser deux ajouts simultanés du même site.
        digest = sha(url.encode())
        source = db.scalar(
            select(Source).where(Source.url_hash == digest).with_for_update()
        )
        if source is None:
            source = Source(
                id=uid(),
                url=url,
                url_hash=digest,
                name=urlsplit(url).hostname,
                hosts=hosts,
                respect_robots=body.respect_robots,
            )
            db.add(source)
            db.flush()
        source.hosts = hosts
        source.respect_robots = body.respect_robots
        job = create_job(db, source, body)
        return {"source_id": source.id, "job_id": job.id}


@app.get("/api/sources", dependencies=[Depends(authenticate)])
def sources():
    with transaction() as db:
        return [
            {
                "id": s.id,
                "url": s.url,
                "name": s.name,
                "respect_robots": s.respect_robots,
            }
            for s in db.scalars(select(Source).order_by(Source.created_at.desc()))
        ]


@app.get("/api/jobs", dependencies=[Depends(authenticate)])
def jobs():
    with transaction() as db:
        result = []
        for job in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)):
            counts = dict(
                db.execute(
                    select(Task.status, func.count())
                    .where(Task.job_id == job.id)
                    .group_by(Task.status)
                ).all()
            )
            linked = select(Task.document_id).where(
                Task.job_id == job.id, Task.document_id.is_not(None)
            )
            docs = db.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.id.in_(linked), Document.kind != "html")
            )
            result.append(
                {
                    "id": job.id,
                    "source_id": job.source_id,
                    "url": db.get(Source, job.source_id).url,
                    "status": job.status,
                    "message": job.message,
                    "policy": job.policy,
                    "counts": counts,
                    "discovered": sum(counts.values()),
                    "documents": docs,
                    "created_at": job.created_at,
                }
            )
        return result


@app.post("/api/jobs/{ident}/{action}", dependencies=[Depends(authenticate)])
def control(ident: str, action: str):
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == ident).with_for_update())
        if not job:
            raise HTTPException(404, "Collecte introuvable.")
        if action == "pause" and job.status in ("queued", "running"):
            job.status = "paused" if job.status == "queued" else "pausing"
        elif action in ("resume", "retry") and job.status in (
            "paused",
            "partial",
            "failed",
        ):
            if action == "retry":
                db.execute(
                    update(Task)
                    .where(Task.job_id == ident, Task.status == "failed")
                    .values(status="pending", attempts=0, error="", ready_at=now())
                )
            # Un budget ne peut augmenter au milieu de la découverte : relancer une nouvelle collecte si limited.
            job.status = "queued"
            job.owner = None
        else:
            raise HTTPException(409, "Action incompatible avec cet état.")
        return {"status": job.status}


@app.get("/api/jobs/{ident}/errors", dependencies=[Depends(authenticate)])
def job_errors(
    ident: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)
):
    with transaction() as db:
        return [
            {
                "url": t.url,
                "error": t.error,
                "http_status": t.http_status,
                "attempts": t.attempts,
            }
            for t in db.scalars(
                select(Task)
                .where(Task.job_id == ident, Task.status == "failed")
                .order_by(Task.id)
                .offset(offset)
                .limit(limit)
            )
        ]


def document_json(db, doc):
    version = db.get(Version, doc.current_version) if doc.current_version else None
    return {
        "id": doc.id,
        "source_id": doc.source_id,
        "url": doc.url,
        "final_url": doc.final_url,
        "title": doc.title,
        "kind": doc.kind,
        "updated_at": doc.updated_at,
        "version_id": doc.current_version,
        "sha256": version.sha256 if version else None,
        "size": version.size if version else None,
        "extraction_status": version.extraction_status if version else None,
        "extraction_error": version.extraction_error if version else None,
        "file_url": f"/api/documents/{doc.id}/file",
        "text_url": f"/api/documents/{doc.id}/text",
    }


@app.get("/api/documents", dependencies=[Depends(authenticate)])
def documents(
    source_id: str | None = None,
    q: str = "",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    with transaction() as db:
        query = select(Document)
        if source_id:
            query = query.where(Document.source_id == source_id)
        if q:
            query = query.where(
                Document.title.ilike("%" + q + "%") | Document.url.ilike("%" + q + "%")
            )
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        return {
            "total": total,
            "items": [
                document_json(db, d)
                for d in db.scalars(
                    query.order_by(Document.updated_at.desc(), Document.id)
                    .offset(offset)
                    .limit(limit)
                )
            ],
        }


@app.get("/api/documents/{ident}", dependencies=[Depends(authenticate)])
def document(ident: str):
    with transaction() as db:
        doc = db.get(Document, ident)
        if not doc:
            raise HTTPException(404, "Document introuvable.")
        return document_json(db, doc)


@app.get("/api/documents/{ident}/file", dependencies=[Depends(authenticate)])
def file(ident: str, version_id: str | None = None):
    with transaction() as db:
        doc = db.get(Document, ident)
        if not doc:
            raise HTTPException(404, "Document introuvable.")
        version = db.get(Version, version_id or doc.current_version)
        if not version or version.document_id != ident:
            raise HTTPException(404, "Version introuvable.")
        path = resolve(version.blob_path)
        if not path.is_file():
            raise HTTPException(410, "Original absent du stockage.")
        return FileResponse(
            path, media_type=version.mime, filename=Path(version.export_path).name
        )


@app.get("/api/documents/{ident}/versions", dependencies=[Depends(authenticate)])
def versions(ident: str):
    with transaction() as db:
        return [
            {
                "id": v.id,
                "sha256": v.sha256,
                "size": v.size,
                "created_at": v.created_at,
                "extraction_status": v.extraction_status,
            }
            for v in db.scalars(
                select(Version)
                .where(Version.document_id == ident)
                .order_by(Version.created_at.desc())
            )
        ]


@app.get("/api/documents/{ident}/text", dependencies=[Depends(authenticate)])
def text(ident: str, version_id: str | None = None):
    with transaction() as db:
        doc = db.get(Document, ident)
        if not doc:
            raise HTTPException(404, "Document introuvable.")
        version = db.get(Version, version_id or doc.current_version)
        if not version or version.document_id != ident:
            raise HTTPException(404, "Version introuvable.")
        if not version.text_path:
            raise HTTPException(
                409, f"Texte non disponible : {version.extraction_status}"
            )
        path = resolve(version.text_path)
        if not path.is_file():
            raise HTTPException(410, "Texte absent du stockage.")
        return {
            "document_id": ident,
            "version_id": version.id,
            "status": version.extraction_status,
            **json.loads(path.read_text(encoding="utf-8")),
        }


@app.get("/api/changes", dependencies=[Depends(authenticate)])
def changes(cursor: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    with transaction() as db:
        rows = db.scalars(
            select(Change).where(Change.id > cursor).order_by(Change.id).limit(limit)
        ).all()
        return {
            "next_cursor": rows[-1].id if rows else cursor,
            "items": [
                {
                    "cursor": r.id,
                    "document_id": r.document_id,
                    "version_id": r.version_id,
                    "event": r.event,
                    "created_at": r.created_at,
                }
                for r in rows
            ],
        }


@app.get("/api/sources/{ident}/export", dependencies=[Depends(authenticate)])
def export(ident: str):
    temporary = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    temporary.close()
    path = Path(temporary.name)
    try:
        with transaction() as db, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            if db.bind.dialect.name == "postgresql":
                db.execute(sql_text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            included = set()
            source = db.get(Source, ident)
            if not source:
                raise HTTPException(404, "Site introuvable.")
            manifest = []
            for doc in db.scalars(select(Document).where(Document.source_id == ident)):
                manifest.append(document_json(db, doc))
                for version in db.scalars(
                    select(Version).where(Version.document_id == doc.id)
                ):
                    if (
                        version.export_path not in included
                        and resolve(version.blob_path).is_file()
                    ):
                        z.write(resolve(version.blob_path), version.export_path)
                        included.add(version.export_path)
                    if (
                        version.text_path
                        and version.text_path not in included
                        and resolve(version.text_path).is_file()
                    ):
                        z.write(resolve(version.text_path), version.text_path)
                        included.add(version.text_path)
            z.writestr(
                "catalogue.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            )
            errors = list(
                db.execute(
                    select(Task.url, Task.error, Task.http_status)
                    .join(Job, Task.job_id == Job.id)
                    .where(Job.source_id == ident, Task.status == "failed")
                ).all()
            )
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["url", "error", "http_status"])
            writer.writerows(errors)
            z.writestr("erreurs.csv", output.getvalue())
            name = safe_name(source.name) + ".zip"
        return FileResponse(
            path,
            media_type="application/zip",
            filename=name,
            background=BackgroundTask(path.unlink, missing_ok=True),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise


@app.get("/health")
def health():
    with transaction() as db:
        db.execute(select(1))
    return {"ok": True}


static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")


@app.get("/")
def home():
    return FileResponse(static / "index.html")
```

## `app/config.py`

```python
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./collector.db")
    )
    storage: Path = field(
        default_factory=lambda: Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
    )
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", ""))
    max_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_FILE_BYTES", str(50 * 1024 * 1024)))
    )
    lease_seconds: int = field(
        default_factory=lambda: int(os.getenv("LEASE_SECONDS", "180"))
    )
    delay: float = field(default_factory=lambda: float(os.getenv("CRAWL_DELAY", "1")))
    concurrency: int = field(
        default_factory=lambda: int(os.getenv("CRAWL_CONCURRENCY", "2"))
    )
    extractor: str = field(default_factory=lambda: os.getenv("EXTRACTOR", "basic"))
    secure_cookie: bool = field(
        default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true"
    )
    user_agent: str = "DocumentCollector/1.0"


settings = Settings()
```

## `app/crawl.py`

```python
"""Un processus Scrapy par collecte ; jamais de reactor dans FastAPI."""

import sys
from scrapy.crawler import CrawlerProcess
from .spider import CollectorSpider, scrapy_settings

if __name__ == "__main__":
    job_id, owner = sys.argv[1:3]
    process = CrawlerProcess(scrapy_settings())
    crawler = process.create_crawler(CollectorSpider)

    # Le prédicat a besoin de la politique de la collecte avant les requêtes navigateur.
    async def abort(request):
        return await crawler.spider.abort_browser(request)

    crawler.settings.set("PLAYWRIGHT_ABORT_REQUEST", abort, priority="cmdline")
    process.crawl(crawler, job_id=job_id, owner=owner)
    process.start()
```

## `app/db.py`

```python
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30}
    if settings.database_url.startswith("sqlite")
    else {},
)
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def sqlite_settings(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")


Session = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction():
    with Session.begin() as db:
        yield db
```

## `app/extract.py`

```python
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
```

## `app/init_db.py`

```python
from .db import engine, transaction
from .models import Base, Clock


def initialize():
    Base.metadata.create_all(engine)
    with transaction() as db:
        if db.get(Clock, 1) is None:
            db.add(Clock(id=1, value=0))


if __name__ == "__main__":
    initialize()
    print("Base initialisée.")
```

## `app/models.py`

```python
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(250))
    hosts: Mapped[list] = mapped_column(JSON)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    policy: Mapped[dict] = mapped_column(JSON)
    owner: Mapped[str | None] = mapped_column(String(36))
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text, default="En attente")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "url_hash"),
        Index("task_queue", "job_id", "status", "ready_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    parent_url: Mapped[str] = mapped_column(Text, default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    http_status: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16), default="http")
    error: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[str | None] = mapped_column(String(36))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "url_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    final_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    current_version: Mapped[str | None] = mapped_column(String(36))
    etag: Mapped[str] = mapped_column(Text, default="")
    last_modified: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("document_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(24))
    blob_path: Mapped[str] = mapped_column(Text)
    export_path: Mapped[str] = mapped_column(Text)
    extraction_status: Mapped[str] = mapped_column(
        String(24), default="pending", index=True
    )
    extraction_owner: Mapped[str | None] = mapped_column(String(36))
    extraction_started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text_path: Mapped[str] = mapped_column(Text, default="")
    extraction_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Clock(Base):
    __tablename__ = "sync_clock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class Change(Base):
    __tablename__ = "changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    event: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
```

## `app/repository.py`

```python
from datetime import timedelta
from sqlalchemy import select, func, update
from .db import transaction
from .models import Job, Task, Document, Version, Clock, Change, now, uid
from .storage import sha
from .urls import normalize


class LostLease(RuntimeError):
    pass


def locked_job(db, job_id, owner):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.owner != owner or job.status not in ("running", "pausing"):
        raise LostLease("Le worker ne possède plus cette collecte.")
    return job


def enqueue(db, job, urls, depth=0, parent=""):
    from urllib.parse import urlsplit
    from .storage import DOCUMENT_EXT

    candidates = {}
    for candidate in urls:
        try:
            url = normalize(candidate)
        except ValueError:
            continue
        if urlsplit(url).hostname in job.policy["hosts"]:
            candidates.setdefault(sha(url.encode()), url)
    if not candidates:
        return
    existing = set()
    hashes = list(candidates)
    for start in range(0, len(hashes), 500):
        existing.update(
            db.scalars(
                select(Task.url_hash).where(
                    Task.job_id == job.id,
                    Task.url_hash.in_(hashes[start : start + 500]),
                )
            )
        )
    total = db.scalar(
        select(func.count()).select_from(Task).where(Task.job_id == job.id)
    )
    limit = job.policy["max_urls"]
    for digest, url in candidates.items():
        if digest in existing:
            continue
        if limit and total >= limit:
            job.message = "Budget de liens atteint ; couverture limitée."
            job.policy = {**job.policy, "limited": True}
            break
        total += 1
        db.add(
            Task(
                job_id=job.id,
                url=url,
                url_hash=digest,
                depth=depth,
                parent_url=parent,
                priority=0
                if urlsplit(url).path.lower().endswith(tuple(DOCUMENT_EXT))
                else 1,
            )
        )


def emit(db, document_id, version_id, event):
    # La mise à jour verrouille la ligne jusqu'au commit : ordre des curseurs = ordre des commits.
    value = db.scalar(
        update(Clock)
        .where(Clock.id == 1)
        .values(value=Clock.value + 1)
        .returning(Clock.value)
    )
    if value is None:
        raise RuntimeError("Initialiser la base avec python -m app.init_db.")
    db.add(
        Change(id=value, document_id=document_id, version_id=version_id, event=event)
    )


def claim_job(owner, lease_seconds):
    with transaction() as db:
        stale = db.scalars(
            select(Job)
            .where(
                Job.status.in_(["running", "pausing"]),
                Job.heartbeat < now() - timedelta(seconds=lease_seconds),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.status = "paused" if job.status == "pausing" else "queued"
            job.owner = None
            db.execute(
                update(Task)
                .where(Task.job_id == job.id, Task.status == "processing")
                .values(status="pending")
            )
        job = db.scalar(
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.status, job.owner, job.heartbeat = "running", owner, now()
        job.message = "Démarrage du worker"
        return job.id


def claim_tasks(job_id, owner, count):
    with transaction() as db:
        job = locked_job(db, job_id, owner)
        if job.status == "pausing":
            return []
        tasks = db.scalars(
            select(Task)
            .where(
                Task.job_id == job_id, Task.status == "pending", Task.ready_at <= now()
            )
            .order_by(Task.priority, Task.id)
            .with_for_update(skip_locked=True)
            .limit(count)
        ).all()
        for task in tasks:
            task.status = "processing"
            task.attempts += 1
        return [
            {"id": t.id, "url": t.url, "depth": t.depth, "parent_url": t.parent_url}
            for t in tasks
        ]


def record_failure(job_id, owner, task_id, error, status=None, retry=False):
    with transaction() as db:
        locked_job(db, job_id, owner)
        task = db.get(Task, task_id)
        task.error, task.http_status = str(error)[:2000], status
        if retry and task.attempts < 3:
            task.status = "pending"
            task.ready_at = now() + timedelta(seconds=min(60, 2**task.attempts * 2))
        else:
            task.status = "failed"


def record_document(
    job_id, owner, task_id, final, title, kind, mime, body, stored, headers, links
):
    digest, blob, export = stored
    with transaction() as db:
        job = locked_job(db, job_id, owner)
        task = db.get(Task, task_id)
        doc = db.scalar(
            select(Document).where(
                Document.source_id == job.source_id, Document.url_hash == task.url_hash
            )
        )
        if doc is None:
            doc = Document(
                id=uid(),
                source_id=job.source_id,
                url=task.url,
                url_hash=task.url_hash,
                final_url=final,
                title=title,
                kind=kind,
            )
            db.add(doc)
            db.flush()
        doc.final_url, doc.title, doc.kind, doc.updated_at = final, title, kind, now()
        doc.etag = headers.get("etag", "")
        doc.last_modified = headers.get("last-modified", "")
        version = db.scalar(
            select(Version).where(
                Version.document_id == doc.id, Version.sha256 == digest
            )
        )
        if version is None:
            version = Version(
                id=uid(),
                document_id=doc.id,
                sha256=digest,
                size=len(body),
                mime=mime,
                kind=kind,
                blob_path=blob,
                export_path=export,
            )
            db.add(version)
            db.flush()
        changed = doc.current_version != version.id
        doc.current_version = version.id
        if changed:
            emit(db, doc.id, version.id, "document.updated")
        task.status = "downloaded" if changed else "unchanged"
        task.document_id, task.http_status, task.error = (
            doc.id,
            headers.get("_status", 200),
            "",
        )
        enqueue(db, job, links, task.depth + 1, final)
        job.message = final
        return doc.id


def finish_job(job_id, owner, error=""):
    with transaction() as db:
        job = locked_job(db, job_id, owner)
        db.execute(
            update(Task)
            .where(Task.job_id == job_id, Task.status == "processing")
            .values(status="pending")
        )
        counts = dict(
            db.execute(
                select(Task.status, func.count())
                .where(Task.job_id == job_id)
                .group_by(Task.status)
            ).all()
        )
        good = counts.get("downloaded", 0) + counts.get("unchanged", 0)
        if job.status == "pausing":
            state = "paused"
        elif error:
            state = "failed"
        elif counts.get("pending"):
            state = "failed"
        elif counts.get("failed"):
            state = "partial" if good else "failed"
        elif job.policy.get("limited"):
            state = "limited"
        else:
            state = "completed"
        job.status = state
        job.owner = None
        job.message = (
            error
            or f"{good} ressources récupérées ; {counts.get('failed', 0)} échecs ; {counts.get('pending', 0)} restantes"
        )
```

## `app/spider.py`

```python
import asyncio
import gzip
import io
import logging
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit, unquote
import scrapy
from scrapy.utils.defer import maybe_deferred_to_future
from .config import settings
from .db import transaction
from .models import Job, Task, Document
from .repository import (
    claim_tasks,
    locked_job,
    record_failure,
    record_document,
    enqueue,
    LostLease,
    finish_job,
)
from .storage import classify, store_original, sha
from .urls import Policy, public_address, PolicyError
from .adapters import html_links, xml_links
from sqlalchemy import select, func

log = logging.getLogger(__name__)


class ScopeMiddleware:
    def __init__(self, crawler):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    async def process_request(self, request, spider=None):
        spider = spider or self.crawler.spider
        delay = await asyncio.to_thread(spider.policy.check, request.url)
        if delay:
            await asyncio.sleep(delay)


class CollectorSpider(scrapy.Spider):
    name = "collector"

    def __init__(self, job_id, owner, **kwargs):
        super().__init__(**kwargs)
        self.job_id, self.owner = job_id, owner
        with transaction() as db:
            job = locked_job(db, job_id, owner)
            self.rules = job.policy
            self.source_id = job.source_id
        self.policy = Policy(self.rules["hosts"], self.rules["respect_robots"])

    async def abort_browser(self, request):
        try:
            await asyncio.to_thread(public_address, request.url)
            if request.is_navigation_request():
                await asyncio.to_thread(self.policy.check, request.url)
            return request.resource_type in ("image", "font", "media")
        except Exception:
            return True

    async def start(self):
        try:
            while True:
                tasks = claim_tasks(self.job_id, self.owner, settings.concurrency)
                if tasks:
                    await asyncio.gather(*(self.fetch_task(task) for task in tasks))
                    continue
                with transaction() as db:
                    job = locked_job(db, self.job_id, self.owner)
                    pending = db.scalar(
                        select(func.count())
                        .select_from(Task)
                        .where(Task.job_id == job.id, Task.status == "pending")
                    )
                    if job.status == "pausing" or not pending:
                        break
                await asyncio.sleep(1)
            finish_job(self.job_id, self.owner)
        except LostLease:
            log.warning("Collecte remplacée par un autre worker.")
        except Exception as exc:
            log.exception("Échec du moteur")
            try:
                finish_job(self.job_id, self.owner, str(exc))
            except LostLease:
                pass
        if False:
            yield scrapy.Request("https://example.com")

    async def download(self, url, browser=False, referer="", headers=None):
        meta = {
            "handle_httpstatus_all": True,
            "download_maxsize": settings.max_bytes,
            "playwright": browser,
        }
        if browser:
            meta.update(
                playwright_context=self.source_id,
                playwright_context_kwargs={
                    "user_agent": settings.user_agent,
                    "service_workers": "block",
                    "accept_downloads": True,
                },
                playwright_page_goto_kwargs={
                    "wait_until": "domcontentloaded",
                    "timeout": 45000,
                },
            )
            from scrapy_playwright.page import PageMethod

            meta["playwright_page_methods"] = [PageMethod("wait_for_timeout", 1500)]
        request = scrapy.Request(
            url,
            headers={**(headers or {}), **({"Referer": referer} if referer else {})},
            meta=meta,
            dont_filter=True,
        )
        engine = self.crawler.engine
        if hasattr(engine, "download_async"):
            return await engine.download_async(request)
        return await maybe_deferred_to_future(engine.download(request))

    async def fetch_task(self, task):
        browser = self.rules["mode"] == "browser"
        try:
            with transaction() as db:
                job = locked_job(db, self.job_id, self.owner)
                db.get(Task, task["id"]).method = "browser" if browser else "http"
                previous = db.scalar(
                    select(Document).where(
                        Document.source_id == self.source_id,
                        Document.url_hash == sha(task["url"].encode()),
                    )
                )
                headers = {}
                if previous:
                    if previous.etag:
                        headers["If-None-Match"] = previous.etag
                    if previous.last_modified:
                        headers["If-Modified-Since"] = previous.last_modified
            response = await self.download(
                task["url"], browser, task["parent_url"], headers if not browser else {}
            )
            if self.rules["mode"] == "auto" and response.status in (403, 406):
                browser = True
                response = await self.download(task["url"], True, task["parent_url"])
            if response.status == 304:
                with transaction() as db:
                    locked_job(db, self.job_id, self.owner)
                    doc = db.scalar(
                        select(Document).where(
                            Document.source_id == self.source_id,
                            Document.url_hash == sha(task["url"].encode()),
                        )
                    )
                    if not doc or not doc.current_version:
                        raise ValueError("Réponse 304 sans original connu.")
                    row = db.get(Task, task["id"])
                    row.status = "unchanged"
                    row.http_status = 304
                    row.document_id = doc.id
                    row.error = ""
                    # Re-parcourt les liens du HTML conservé pour ne pas perdre la couverture.
                    if doc.kind == "html":
                        from .models import Version
                        from .storage import resolve

                        version = db.get(Version, doc.current_version)
                        _, links = html_links(
                            resolve(version.blob_path).read_bytes(), doc.final_url
                        )
                        enqueue(
                            db,
                            db.get(Job, self.job_id),
                            links,
                            task["depth"] + 1,
                            doc.final_url,
                        )
                return
            if response.status == 404 and urlsplit(task["url"]).path in (
                "/sitemap.xml",
                "/sitemap_index.xml",
                "/rss.xml",
            ):
                with transaction() as db:
                    locked_job(db, self.job_id, self.owner)
                    row = db.get(Task, task["id"])
                    row.status = "skipped"
                    row.error = "Index facultatif absent"
                    row.http_status = 404
                return
            if response.status >= 400:
                record_failure(
                    self.job_id,
                    self.owner,
                    task["id"],
                    f"HTTP {response.status}",
                    response.status,
                    retry=response.status in (408, 425, 429, 500, 502, 503, 504),
                )
                return
            await asyncio.to_thread(self.policy.check, response.url)
            body = response.body
            disposition = Message()
            disposition["content-disposition"] = response.headers.get(
                b"Content-Disposition", b""
            ).decode("latin-1")
            suggested = (
                response.meta.get("playwright_suggested_filename", "")
                or disposition.get_filename()
                or ""
            )
            mime = response.headers.get(b"Content-Type", b"").decode("latin-1")
            kind, mime = classify(body, mime, response.url, suggested)
            title = (
                suggested
                or unquote(Path(urlsplit(response.url).path).name)
                or response.url
            )
            links = []
            if kind == "html":
                title, links = html_links(body, response.url)
                if self.rules["mode"] == "auto" and not browser:
                    from bs4 import BeautifulSoup

                    text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
                    if len(text) < 150:
                        response = await self.download(
                            task["url"], True, task["parent_url"]
                        )
                        browser = True
                        if response.status >= 400:
                            raise ValueError(
                                f"Rendu navigateur : HTTP {response.status}"
                            )
                        await asyncio.to_thread(self.policy.check, response.url)
                        body = response.body
                        mime = response.headers.get(b"Content-Type", b"").decode(
                            "latin-1"
                        )
                        kind, mime = classify(
                            body,
                            mime,
                            response.url,
                            response.meta.get("playwright_suggested_filename", ""),
                        )
                        if kind == "html":
                            title, links = html_links(body, response.url)
            if kind == "index":
                if body[:2] == b"\x1f\x8b":
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:
                        body = f.read(settings.max_bytes + 1)
                    if len(body) > settings.max_bytes:
                        raise ValueError("Index décompressé trop volumineux.")
                links = xml_links(body, response.url)
                with transaction() as db:
                    job = locked_job(db, self.job_id, self.owner)
                    enqueue(db, job, links, task["depth"] + 1, response.url)
                    row = db.get(Task, task["id"])
                    row.status = "index"
                    row.http_status = response.status
                    row.error = ""
                return
            stored = await asyncio.to_thread(
                store_original, body, self.source_id, response.url, kind, suggested
            )
            response_headers = {
                k.decode("latin-1").lower(): response.headers.get(k, b"").decode(
                    "latin-1"
                )
                for k in response.headers
            }
            response_headers["_status"] = response.status
            record_document(
                self.job_id,
                self.owner,
                task["id"],
                response.url,
                title,
                kind,
                mime,
                body,
                stored,
                response_headers,
                links,
            )
            with transaction() as db:
                locked_job(db, self.job_id, self.owner)
                db.get(Task, task["id"]).method = "browser" if browser else "http"
        except LostLease:
            raise
        except Exception as exc:
            # Erreurs de politique / format : pas de nouvelles tentatives automatiques.
            retry = not isinstance(exc, (ValueError, PolicyError))
            record_failure(self.job_id, self.owner, task["id"], str(exc), retry=retry)


def scrapy_settings():
    return {
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "DOWNLOADER_MIDDLEWARES": {
            "app.spider.ScopeMiddleware": 100,
            "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
            "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
        },
        "ROBOTSTXT_OBEY": False,  # Politique commune HTTP + navigateur dans ScopeMiddleware / abort_browser.
        "USER_AGENT": settings.user_agent,
        "DOWNLOAD_TIMEOUT": 60,
        "DOWNLOAD_MAXSIZE": settings.max_bytes,
        "CONCURRENT_REQUESTS": settings.concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "DOWNLOAD_DELAY": settings.delay,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": settings.delay,
        "AUTOTHROTTLE_MAX_DELAY": 30,
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_MAX_CONTEXTS": 2,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 2,
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 45000,
        "PLAYWRIGHT_PROCESS_REQUEST_HEADERS": None,
        "LOG_LEVEL": "INFO",
        "TELNETCONSOLE_ENABLED": False,
    }
```

## `app/static/app.js`

```javascript
const $ = id => document.getElementById(id);
let offset = 0,
    busy = false,
    searchTimer;
const limit = 30;
const labels = {
    queued: 'En attente',
    running: 'En cours',
    pausing: 'Pause demandée',
    paused: 'En pause',
    completed: 'Terminée',
    partial: 'Partielle',
    failed: 'Échec',
    limited: 'Budget atteint',
    done: 'Texte prêt',
    needs_ocr: 'OCR nécessaire',
    pending: 'Extraction en attente',
    processing: 'Extraction en cours',
    unsupported: 'Original disponible, extraction non prise en charge',
    empty: 'Texte vide'
};

function node(tag, text, cls) {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
}

function notify(text) {
    $('message').textContent = text;
    $('message').hidden = false;
    setTimeout(() => $('message').hidden = true, 6000);
}
async function api(path, options = {}) {
    const response = await fetch(path, {
        credentials: 'same-origin',
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        }
    });
    if (response.status === 401) {
        $('login').hidden = false;
        $('workspace').hidden = true;
        throw new Error('Connecte-toi avec ta clé.');
    }
    if (!response.ok) {
        const data = await response.json().catch(() => ({
            detail: 'Erreur du serveur'
        }));
        throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    }
    return response.json();
}
$('login-form').onsubmit = async event => {
    event.preventDefault();
    try {
        await api('/api/login', {
            method: 'POST',
            body: JSON.stringify({
                key: $('key').value
            })
        });
        $('key').value = '';
        await loadSources();
        await refresh();
    } catch (e) {
        notify(e.message);
    }
};
$('logout').onclick = async () => {
    await api('/api/logout', {
        method: 'POST'
    });
    location.reload();
};
$('source-form').onsubmit = async event => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
        await api('/api/sources', {
            method: 'POST',
            body: JSON.stringify({
                url: $('url').value,
                respect_robots: $('robots').checked,
                max_urls: Number($('budget').value),
                mode: $('mode').value,
                extra_hosts: $('hosts').value.split(',').map(x => x.trim()).filter(Boolean)
            })
        });
        $('url').value = '';
        notify('Site ajouté à la file.');
        await loadSources();
        await refresh();
    } catch (e) {
        notify(e.message);
    } finally {
        button.disabled = false;
    }
};
async function loadSources() {
    const items = await api('/api/sources');
    const selected = $('source').value;
    $('source').replaceChildren(node('option', 'Tous les sites'));
    $('source').firstChild.value = '';
    for (const source of items) {
        const option = node('option', source.name);
        option.value = source.id;
        $('source').append(option);
    }
    if (items.some(s => s.id === selected)) $('source').value = selected;
    $('login').hidden = true;
    $('workspace').hidden = false;
    $('logout').hidden = false;
}
async function action(id, name) {
    try {
        await api(`/api/jobs/${id}/${name}`, {
            method: 'POST'
        });
        await refresh();
    } catch (e) {
        notify(e.message);
    }
}
async function loadJobs() {
    const jobs = await api('/api/jobs');
    const area = $('jobs');
    area.replaceChildren();
    if (!jobs.length) {
        area.append(node('p', 'Aucune collecte. Ajoute un lien pour commencer.', 'hint'));
        return;
    }
    for (const job of jobs) {
        const row = node('div', undefined, 'job');
        const heading = node('div', undefined, 'heading');
        heading.append(node('div', job.url, 'title'), node('span', labels[job.status] || job.status, 'badge'));
        row.append(heading);
        const c = job.counts;
        row.append(node('p', `${job.discovered} URL découvertes · ${job.documents} documents · ${c.pending||0} en attente · ${c.processing||0} en cours · ${c.failed||0} échecs`, 'hint'), node('div', job.message, 'url'));
        const actions = node('div', undefined, 'actions');
        for (const [cmd, text] of [
                ['pause', 'Pause'],
                ['resume', 'Reprendre'],
                ['retry', 'Réessayer les erreurs']
            ]) {
            const allowed = cmd === 'pause' ? ['queued', 'running'] : ['paused', 'partial', 'failed'];
            if (allowed.includes(job.status)) {
                const button = node('button', text);
                button.onclick = () => action(job.id, cmd);
                actions.append(button);
            }
        }
        if (c.failed) {
            const button = node('button', 'Voir les erreurs');
            button.onclick = async () => {
                try {
                    const errors = await api(`/api/jobs/${job.id}/errors`);
                    showPreview(errors.map(e => `${e.url}\n${e.error}`).join('\n\n'));
                } catch (e) {
                    notify(e.message);
                }
            };
            actions.append(button);
        }
        if (job.status === 'limited') row.append(node('p', 'Relance ce site avec un budget supérieur ou 0 pour élargir la couverture.', 'hint'));
        row.append(actions);
        area.append(row);
    }
}

function showPreview(text) {
    $('preview-text').textContent = text;
    $('preview').showModal();
}
$('close-preview').onclick = () => $('preview').close();
async function loadDocuments() {
    const source = $('source').value;
    const params = new URLSearchParams({
        offset,
        limit,
        q: $('search').value
    });
    if (source) params.set('source_id', source);
    const data = await api('/api/documents?' + params);
    $('count').textContent = `${data.total} ressources archivées · résultats ${data.total?offset+1:0}–${Math.min(offset+limit,data.total)}`;
    $('previous').disabled = offset === 0;
    $('next').disabled = offset + limit >= data.total;
    $('export').hidden = !source;
    if (source) $('export').href = `/api/sources/${source}/export`;
    const area = $('documents');
    area.replaceChildren();
    for (const doc of data.items) {
        const row = node('div', undefined, 'document');
        row.append(node('div', doc.title, 'title'), node('div', doc.url, 'url'), node('span', `${doc.kind.toUpperCase()} · ${Math.round(doc.size/1024)} Ko · ${labels[doc.extraction_status]||doc.extraction_status}`, 'badge'));
        const actions = node('div', undefined, 'actions');
        const link = node('a', 'Télécharger l’original', 'button');
        link.href = doc.file_url;
        actions.append(link);
        const preview = node('button', 'Lire le texte');
        preview.onclick = async () => {
            try {
                const text = await api(doc.text_url);
                showPreview(text.text || 'Aucun texte extrait.');
            } catch (e) {
                notify(e.message);
            }
        };
        actions.append(preview);
        row.append(actions);
        if (doc.extraction_error) row.append(node('p', doc.extraction_error, 'error'));
        area.append(row);
    }
}
$('source').onchange = () => {
    offset = 0;
    loadDocuments().catch(e => notify(e.message));
};
$('search').oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        offset = 0;
        loadDocuments().catch(e => notify(e.message));
    }, 300);
};
$('previous').onclick = () => {
    offset = Math.max(0, offset - limit);
    loadDocuments().catch(e => notify(e.message));
};
$('next').onclick = () => {
    offset += limit;
    loadDocuments().catch(e => notify(e.message));
};
async function refresh() {
        if (busy) return;
        busy = true;
        try {
            await Promise.all([loadJobs(), loadDocuments()]);
            $('activity').textContent = 'Mis à jour à ' + new Date().toLocaleTimeString();
        } finally {
            busy = false;
        }
    }
    (async () => {
        try {
            await loadSources();
            await refresh();
        } catch (e) {
            if (!$('workspace').hidden) notify(e.message);
        }
    })();
setInterval(() => {
    if (!$('workspace').hidden) refresh().catch(e => {
        if (!$('workspace').hidden) notify(e.message);
    });
}, 3000);
```

## `app/static/index.html`

```html
<!DOCTYPE html>
<html lang="fr">
 <head>
  <meta charset="utf-8"/>
  <meta content="width=device-width,initial-scale=1" name="viewport"/>
  <title>
   Collecteur documentaire
  </title>
  <link href="/static/style.css" rel="stylesheet"/>
  <script defer="" src="/static/app.js">
  </script>
 </head>
 <body>
  <header>
   <strong>
    Collecteur documentaire
   </strong>
   <span>
    Vos sources. Vos documents.
   </span>
   <button hidden="" id="logout">
    Déconnexion
   </button>
  </header>
  <main>
   <section id="login">
    <h1>
     Ouvrir le collecteur
    </h1>
    <p>
     Utilise la clé définie dans le fichier .env.
    </p>
    <form id="login-form">
     <label>
      Clé d’accès
      <input autocomplete="current-password" id="key" required="" type="password"/>
     </label>
     <button>
      Se connecter
     </button>
    </form>
   </section>
   <div hidden="" id="workspace">
    <section>
     <h1>
      Ajouter un site
     </h1>
     <form id="source-form">
      <label>
       Lien du site
       <input id="url" placeholder="https://www.cmf.tn/" required="" type="url"/>
      </label>
      <label class="check">
       <input checked="" id="robots" type="checkbox"/>
       Respecter robots.txt
      </label>
      <details>
       <summary>
        Options de collecte
       </summary>
       <div class="options">
        <label>
         Budget d’URL (0 = sans plafond)
         <input id="budget" max="1000000" min="0" type="number" value="1000"/>
        </label>
        <label>
         Chargement
         <select id="mode">
          <option value="auto">
           Automatique
          </option>
          <option value="http">
           HTTP uniquement
          </option>
          <option value="browser">
           Navigateur
          </option>
         </select>
        </label>
        <label>
         Autres hôtes autorisés
         <input id="hosts" placeholder="documents.example.com, cdn.example.com"/>
        </label>
       </div>
      </details>
      <button>
       Ajouter et démarrer
      </button>
      <p class="hint">
       Tu peux ajouter d’autres sites pendant une collecte. Pause, reprise et téléchargement restent accessibles.
      </p>
     </form>
    </section>
    <section>
     <div class="heading">
      <h2>
       Collectes
      </h2>
      <span class="hint" id="activity">
       Actualisation toutes les 3 secondes
      </span>
     </div>
     <div id="jobs">
     </div>
    </section>
    <section>
     <div class="heading">
      <h2>
       Documents et informations
      </h2>
      <a class="button" hidden="" id="export">
       Télécharger tout le site
      </a>
     </div>
     <div class="filters">
      <label>
       Site
       <select id="source">
        <option value="">
         Tous les sites
        </option>
       </select>
      </label>
      <label>
       Rechercher
       <input id="search" placeholder="Titre ou URL"/>
      </label>
     </div>
     <p class="hint" id="count">
     </p>
     <div id="documents">
     </div>
     <div class="heading">
      <button id="previous">
       Précédent
      </button>
      <button id="next">
       Suivant
      </button>
     </div>
    </section>
   </div>
   <div aria-live="polite" hidden="" id="message" role="status">
   </div>
   <dialog id="preview">
    <div class="heading">
     <h2>
      Contenu extrait
     </h2>
     <button id="close-preview">
      Fermer
     </button>
    </div>
    <pre id="preview-text"></pre>
   </dialog>
  </main>
  <footer>
   Les fichiers sont archivés dans le volume storage. Les erreurs de téléchargement et d’extraction sont affichées séparément.
  </footer>
 </body>
</html>
```

## `app/static/style.css`

```css
:root {
    font: 15px/1.55 system-ui, sans-serif;
    color: #192638;
    background: #f3f5f8
}

* {
    box-sizing: border-box
}

body {
    margin: 0
}

header {
    background: #13243a;
    color: white;
    padding: 20px max(24px, calc((100vw - 1120px)/2));
    display: flex;
    align-items: center;
    gap: 22px
}

header span {
    opacity: .7;
    flex: 1
}

main {
    max-width: 1120px;
    margin: 30px auto;
    padding: 0 20px
}

section {
    background: white;
    border: 1px solid #dfe5ec;
    border-radius: 12px;
    padding: 26px;
    margin-bottom: 22px
}

h1 {
    font-size: 26px;
    margin: 0 0 16px
}

h2 {
    font-size: 20px;
    margin: 0 0 12px
}

label {
    display: block;
    font-size: 14px;
    font-weight: 550
}

input,
select {
    font: inherit;
    padding: 11px 12px;
    border: 1px solid #b9c5d2;
    border-radius: 7px;
    width: 100%;
    margin: 6px 0 15px;
    background: white
}

input:focus,
select:focus {
    outline: 2px solid #7ebbf1;
    border-color: #2875bc
}

.check {
    display: flex;
    gap: 9px;
    align-items: center;
    margin: 0 0 14px
}

.check input {
    width: auto;
    margin: 0
}

button,
.button {
    display: inline-block;
    font: inherit;
    background: #1261a8;
    color: white;
    border: 0;
    padding: 10px 15px;
    border-radius: 7px;
    text-decoration: none;
    cursor: pointer
}

button:hover,
.button:hover {
    background: #0c4c85
}

button:disabled {
    opacity: .45;
    cursor: default
}

.hint {
    color: #62748b;
    font-size: 13px
}

details {
    margin: 0 0 18px
}

summary {
    cursor: pointer;
    color: #3e5d7c
}

.options,
.filters {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-top: 12px
}

.options label:last-child {
    grid-column: 1/-1
}

.heading {
    display: flex;
    gap: 12px;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px
}

.heading h2 {
    margin: 0
}

.job,
.document {
    padding: 17px 0;
    border-top: 1px solid #e7ecf1
}

.job:first-child,
.document:first-child {
    border-top: 0
}

.title {
    font-weight: 650;
    overflow-wrap: anywhere
}

.actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 9px
}

.actions button,
.actions a {
    font-size: 13px;
    padding: 7px 12px
}

.badge {
    font-size: 12px;
    border-radius: 20px;
    padding: 4px 9px;
    background: #e8eef5;
    display: inline-block
}

.error {
    color: #a22d36
}

.success {
    color: #1e6b48
}

.url {
    font-size: 12px;
    color: #62748b;
    overflow-wrap: anywhere
}

#message {
    position: fixed;
    bottom: 20px;
    right: 20px;
    max-width: 500px;
    padding: 16px;
    border-radius: 8px;
    color: white;
    background: #13243a;
    display: none
}

dialog {
    border: 1px solid #dfe5ec;
    border-radius: 12px;
    width: min(900px, 95vw);
    max-height: 85vh;
    padding: 22px
}

dialog::backdrop {
    background: #13243a88
}

pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font: 14px/1.65 system-ui
}

footer {
    text-align: center;
    color: #62748b;
    padding: 12px 20px 28px;
    font-size: 12px
}

[hidden] {
    display: none !important
}

@media(max-width:650px) {
    header span {
        display: none
    }

    .options,
    .filters {
        grid-template-columns: 1fr
    }

    section {
        padding: 18px
    }

    .heading {
        align-items: flex-start;
        flex-wrap: wrap
    }
}
```

## `app/storage.py`

```python
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
```

## `app/urls.py`

```python
import ipaddress
import socket
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import httpx
from .config import settings


class PolicyError(ValueError):
    pass


def normalize(url):
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise PolicyError("URL HTTP(S) complète requise, sans identifiants.")
    if p.port not in (None, 80, 443):
        raise PolicyError("Ports 80 et 443 uniquement.")
    if len(url) > 4096:
        raise PolicyError("URL trop longue.")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    netloc = p.hostname.encode("idna").decode().lower()
    if ":" in netloc:
        netloc = "[" + netloc + "]"
    if p.port:
        netloc += ":" + str(p.port)
    return urlunsplit(
        (
            p.scheme,
            netloc,
            quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~"),
            urlencode(query),
            "",
        )
    )


def public_address(url):
    host = urlsplit(normalize(url)).hostname
    addresses = socket.getaddrinfo(host, None)
    if not addresses or any(
        not ipaddress.ip_address(a[4][0]).is_global for a in addresses
    ):
        raise PolicyError("Adresse locale ou privée refusée.")


def allowed_hosts(url, extra):
    host = urlsplit(normalize(url)).hostname
    hosts = {host}
    if host.startswith("www."):
        hosts.add(host[4:])
    else:
        hosts.add("www." + host)
    for value in extra:
        hosts.add(
            urlsplit(
                normalize(value if "://" in value else "https://" + value)
            ).hostname
        )
    return sorted(hosts)


class Policy:
    def __init__(self, hosts, respect):
        self.hosts, self.respect = set(hosts), respect
        self.cache = {}
        self.lock = threading.RLock()

    def scope(self, url):
        url = normalize(url)
        if urlsplit(url).hostname not in self.hosts:
            raise PolicyError("Hôte hors du périmètre autorisé.")
        public_address(url)
        return url

    def robots(self, url):
        p = urlsplit(url)
        origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
        with self.lock:
            if origin in self.cache:
                return self.cache[origin]
            rule = RobotFileParser()
            target = origin + "/robots.txt"
            try:
                with httpx.Client(timeout=15, trust_env=False) as client:
                    for _ in range(6):
                        self.scope(target)
                        with client.stream(
                            "GET", target, headers={"User-Agent": settings.user_agent}
                        ) as response:
                            if response.status_code in (301, 302, 303, 307, 308):
                                target = urljoin(target, response.headers["location"])
                                continue
                            if response.status_code in (404, 410):
                                rule.parse([])
                                break
                            if response.status_code != 200:
                                raise PolicyError(
                                    "robots.txt inaccessible : politique non vérifiable."
                                )
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) > 512 * 1024:
                                    raise PolicyError("robots.txt trop volumineux.")
                            rule.parse(
                                body.decode("utf-8", errors="replace").splitlines()
                            )
                            break
                    else:
                        raise PolicyError("Trop de redirections robots.txt.")
            except Exception:
                self.cache[origin] = None
                raise
            self.cache[origin] = rule
            return rule

    def check(self, url):
        self.scope(url)
        if self.respect:
            rule = self.robots(url)
            if rule is None or not rule.can_fetch(settings.user_agent, url):
                raise PolicyError(
                    "URL exclue par robots.txt ou politique indisponible."
                )
            return rule.crawl_delay(settings.user_agent) or 0
        return 0
```

## `app/worker.py`

```python
import logging
import signal
import subprocess
import sys
import time
from uuid import uuid4
from .config import settings
from .db import transaction
from .models import Job, now
from .repository import claim_job, finish_job, LostLease

log = logging.getLogger(__name__)


def main():
    stop = False
    child = None

    def stopping(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    while not stop:
        owner = str(uuid4())
        job_id = claim_job(owner, settings.lease_seconds)
        if not job_id:
            time.sleep(1)
            continue
        child = subprocess.Popen([sys.executable, "-m", "app.crawl", job_id, owner])
        while child.poll() is None:
            try:
                with transaction() as db:
                    job = db.get(Job, job_id)
                    if not job or job.owner != owner:
                        child.terminate()
                        break
                    job.heartbeat = now()
                    if stop:
                        job.status = "pausing"
            except Exception:
                log.exception("Échec du heartbeat ; le lease permettra la reprise.")
            time.sleep(2)
        # Un processus qui s'arrête sans finaliser ne doit pas laisser un job running.
        with transaction() as db:
            job = db.get(Job, job_id)
            incomplete = job and job.owner == owner
        if incomplete:
            try:
                finish_job(
                    job_id,
                    owner,
                    "Moteur arrêté sans finalisation ; reprise manuelle possible.",
                )
            except LostLease:
                pass
    if child and child.poll() is None:
        child.wait(timeout=90)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
```

## `compose.yaml`

```yaml
x-app: &app
  build:
    context: .
    args:
      INSTALL_DOCLING: ${INSTALL_DOCLING:-false}
  env_file: .env
  environment:
    DATABASE_URL: postgresql+psycopg://collector:${POSTGRES_PASSWORD}@db:5432/collector
    STORAGE_DIR: /storage
  volumes:
    - documents:/storage
  init: true
  restart: unless-stopped
  depends_on:
    init-db:
      condition: service_completed_successfully

services:
  db:
    image: postgres:17-bookworm
    environment:
      POSTGRES_USER: collector
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Définir POSTGRES_PASSWORD dans .env}
      POSTGRES_DB: collector
    volumes:
      - postgres:/var/lib/postgresql/data
    healthcheck:
      test: [CMD-SHELL, 'pg_isready -U collector -d collector']
      interval: 5s
      retries: 20
    restart: unless-stopped
  init-db:
    build:
      context: .
      args:
        INSTALL_DOCLING: ${INSTALL_DOCLING:-false}
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://collector:${POSTGRES_PASSWORD}@db:5432/collector
    command: [python, -m, app.init_db]
    depends_on:
      db:
        condition: service_healthy
    restart: 'no'
  api:
    <<: *app
    ports: ['127.0.0.1:8000:8000']
    healthcheck:
      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
      interval: 15s
      retries: 5
  worker:
    <<: *app
    command: [python, -m, app.worker]
    stop_grace_period: 120s
  extractor:
    <<: *app
    command: [python, -m, app.extract]

volumes:
  postgres:
  documents:
```

## `requirements-dev.txt`

```text
-r requirements.txt
pytest>=8,<10
```

## `requirements-docling.txt`

```text
-r requirements.txt
docling>=2,<3
```

## `requirements.txt`

```text
fastapi==0.162.0
uvicorn[standard]==0.54.0
SQLAlchemy==2.0.57
psycopg[binary]==3.3.8
httpx==0.28.1
Scrapy==2.19.0
scrapy-playwright==0.0.48
pypdf==6.10.0
python-docx==1.2.0
beautifulsoup4==4.15.0
defusedxml==0.7.1
```

## `scripts/setup.py`

```python
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    raise SystemExit(".env existe déjà ; aucun secret remplacé.")
text = (root / ".env.example").read_text()
text = text.replace("API_KEY=\n", "API_KEY=" + secrets.token_urlsafe(32) + "\n")
text = text.replace(
    "POSTGRES_PASSWORD=\n", "POSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n"
)
path.write_text(text)
try:
    path.chmod(0o600)
except OSError:
    pass
print(".env créé. La clé de connexion est dans API_KEY. Ne pas publier ce fichier.")
```

## `tests/__init__.py`

```python

```

## `tests/conftest.py`

```python
import os
import tempfile
from pathlib import Path
import pytest

TEMP = tempfile.TemporaryDirectory()
test_url = os.getenv("TEST_DATABASE_URL")
if test_url and not test_url.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError(
        "TEST_DATABASE_URL doit cibler une base dédiée finissant par _test."
    )
os.environ["DATABASE_URL"] = test_url or "sqlite:///" + str(Path(TEMP.name) / "test.db")
os.environ["STORAGE_DIR"] = str(Path(TEMP.name) / "storage")
os.environ["API_KEY"] = "test-key-only-abcdefghijklmnopqrstuvwxyz"

from app.db import engine, transaction
from app.models import Base, Clock
from app.api import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with transaction() as db:
        db.add(Clock(id=1, value=0))
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.api.public_address", lambda url: None)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def authorized(client):
    client.headers["X-API-Key"] = os.environ["API_KEY"]
    return client
```

## `tests/crawl_fixture.py`

```python
"""Entrée de test uniquement : autorise un serveur HTTP de fixture sur loopback."""

import os
import app.repository
from tests.fixture_urls import fixture_normalizer
import app.urls
import app.spider

normalizer = fixture_normalizer(int(os.environ["FIXTURE_PORT"]))
app.urls.normalize = normalizer
app.repository.normalize = normalizer
app.urls.public_address = lambda url: None
app.spider.public_address = lambda url: None
from scrapy.crawler import CrawlerProcess
from app.spider import CollectorSpider, scrapy_settings
import sys

if __name__ == "__main__":
    process = CrawlerProcess(scrapy_settings())
    crawler = process.create_crawler(CollectorSpider)

    async def abort(request):
        return await crawler.spider.abort_browser(request)

    crawler.settings.set("PLAYWRIGHT_ABORT_REQUEST", abort, priority="cmdline")
    process.crawl(crawler, job_id=sys.argv[1], owner=sys.argv[2])
    process.start()
```

## `tests/fixture_urls.py`

```python
"""Politique de port de test seulement ; jamais importée par app/."""

from urllib.parse import urlsplit, urlunsplit
from app.urls import normalize as original


def fixture_normalizer(port):
    def normalize(url):
        p = urlsplit(url)
        if p.hostname == "127.0.0.1" and p.port == port:
            clean = original(
                urlunsplit((p.scheme, p.hostname, p.path, p.query, p.fragment))
            )
            c = urlsplit(clean)
            return urlunsplit((c.scheme, p.netloc, c.path, c.query, c.fragment))
        return original(url)

    return normalize
```

## `tests/test_http_integration.py`

```python
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
```

## `tests/test_system.py`

```python
import io
import zipfile
from datetime import timedelta
from pypdf import PdfWriter
from docx import Document as Word
from sqlalchemy import select
from app.config import settings
from app.db import transaction
from app.models import Job, Task, Version, Change, now
from app.repository import (
    claim_job,
    claim_tasks,
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
```
