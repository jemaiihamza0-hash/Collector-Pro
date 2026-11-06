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
