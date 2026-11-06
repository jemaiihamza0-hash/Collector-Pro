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
