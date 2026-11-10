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
from .models import Source, Job, Task, Document, Version, Change, Schedule, now, uid
from .repository import active_job, create_job, configure_schedule
from .urls import normalize, allowed_hosts, public_address
from .storage import sha, resolve, safe_name


@asynccontextmanager
async def lifespan(app):
    if len(settings.api_key) < 24:
        raise RuntimeError("Configurer une API_KEY aléatoire de 24 caractères minimum.")
    if settings.reader_api_key and (
        len(settings.reader_api_key) < 24 or settings.reader_api_key == settings.api_key
    ):
        raise RuntimeError(
            "READER_API_KEY doit être distincte et faire au moins 24 caractères."
        )
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
        if origin.rstrip("/") != (
            settings.public_origin or str(request.base_url).rstrip("/")
        ):
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


def authenticate_reader(request: Request):
    key = request.headers.get("X-API-Key", "")
    if settings.reader_api_key and secrets.compare_digest(
        key.encode(), settings.reader_api_key.encode()
    ):
        return
    authenticate(request)


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
    interval_minutes: int | None = Field(default=None, ge=0, le=525600)


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
        if active_job(db, source.id):
            raise HTTPException(409, "Ce site a déjà une collecte active ou en pause.")
        job = create_job(db, source, body.max_urls, body.mode)
        schedule = db.get(Schedule, source.id)
        if body.interval_minutes is not None or schedule:
            configure_schedule(
                db,
                source.id,
                body.interval_minutes
                if body.interval_minutes is not None
                else schedule.interval_minutes,
                body.max_urls,
                body.mode,
            )
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
                "interval_minutes": schedule.interval_minutes if schedule else 0,
                "next_run": schedule.next_run if schedule else None,
            }
            for s, schedule in db.execute(
                select(Source, Schedule)
                .outerjoin(Schedule)
                .order_by(Source.created_at.desc())
            )
        ]


class ScheduleUpdate(BaseModel):
    interval_minutes: int = Field(ge=0, le=525600)


@app.patch("/api/sources/{ident}/schedule", dependencies=[Depends(authenticate)])
def update_schedule(ident: str, body: ScheduleUpdate):
    with transaction() as db:
        source = db.scalar(select(Source).where(Source.id == ident).with_for_update())
        if not source:
            raise HTTPException(404, "Site introuvable.")
        latest = db.scalar(
            select(Job)
            .where(Job.source_id == ident)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        schedule = configure_schedule(
            db,
            ident,
            body.interval_minutes,
            latest.policy["max_urls"] if latest else 1000,
            latest.policy["mode"] if latest else "auto",
        )
        return {
            "interval_minutes": body.interval_minutes,
            "next_run": schedule.next_run if schedule else None,
        }


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
        source_id = db.scalar(select(Job.source_id).where(Job.id == ident))
        if source_id is not None:
            db.scalar(select(Source).where(Source.id == source_id).with_for_update())
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
            if active_job(db, job.source_id, except_id=job.id):
                raise HTTPException(409, "Une autre collecte est active pour ce site.")
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


@app.get("/api/documents", dependencies=[Depends(authenticate_reader)])
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


@app.get("/api/documents/{ident}", dependencies=[Depends(authenticate_reader)])
def document(ident: str):
    with transaction() as db:
        doc = db.get(Document, ident)
        if not doc:
            raise HTTPException(404, "Document introuvable.")
        return document_json(db, doc)


@app.get("/api/documents/{ident}/file", dependencies=[Depends(authenticate_reader)])
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


@app.get("/api/documents/{ident}/versions", dependencies=[Depends(authenticate_reader)])
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


@app.get("/api/documents/{ident}/text", dependencies=[Depends(authenticate_reader)])
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


@app.get("/api/changes", dependencies=[Depends(authenticate_reader)])
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
                entry = document_json(db, doc)
                manifest.append(entry)
                for version in db.scalars(
                    select(Version).where(Version.document_id == doc.id)
                ):
                    if (
                        version.export_path not in included
                        and resolve(version.blob_path).is_file()
                    ):
                        z.write(resolve(version.blob_path), version.export_path)
                        included.add(version.export_path)
                    if version.kind == "pdf" and version.id == doc.current_version:
                        if version.export_path in included:
                            entry["pdf_path"] = version.export_path
                    if (
                        version.text_path
                        and version.text_path not in included
                        and resolve(version.text_path).is_file()
                    ):
                        z.write(resolve(version.text_path), version.text_path)
                        included.add(version.text_path)
                    if version.kind == "pdf" and version.text_path:
                        text_file = resolve(version.text_path)
                        if text_file.is_file():
                            stem = Path(version.export_path).stem.rsplit("__", 1)[0]
                            plain_path = f"text/{stem}.txt"
                            suffix = 2
                            while plain_path in included:
                                plain_path = f"text/{stem}-{suffix}.txt"
                                suffix += 1
                            payload = json.loads(text_file.read_text(encoding="utf-8"))
                            z.writestr(plain_path, payload["text"])
                            included.add(plain_path)
                            if version.id == doc.current_version:
                                entry["txt_path"] = plain_path
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
