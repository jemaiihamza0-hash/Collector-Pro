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
