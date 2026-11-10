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
    public_origin: str = field(
        default_factory=lambda: os.getenv("PUBLIC_ORIGIN", "").rstrip("/")
    )
    reader_api_key: str = field(default_factory=lambda: os.getenv("READER_API_KEY", ""))
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
