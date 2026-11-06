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
