from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base, get_db
from app.main import create_app
from app.security import _rate_events


@pytest.fixture()
def sqlite_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as session:
        yield session


@pytest.fixture()
def client(sqlite_session: Session) -> Iterator[TestClient]:
    _rate_events.clear()
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        secret_key="test-secret-not-for-production",
        secure_cookies=False,
        trusted_proxy_cidrs="0.0.0.0/0,::/0",
    )
    app = create_app(settings)

    def override_db() -> Iterator[Session]:
        yield sqlite_session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client


def signup_and_login(client: TestClient, username: str = "학생 A", password: str = "pw") -> dict:
    res = client.post("/api/signup", json={"username": username, "password": password})
    assert res.status_code == 201, res.text
    csrf = res.json()["csrf_token"]
    me = client.get("/api/me")
    assert me.status_code == 200
    return {"csrf": csrf, "user": me.json()}


def require_postgres_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture()
def live_server_url(sqlite_session: Session) -> Iterator[str]:
    import_bundle_late = __import__("app.importer", fromlist=["import_bundle"]).import_bundle
    import_bundle_late(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        secret_key="live-test-secret",
        secure_cookies=False,
    )
    app = create_app(settings)

    def override_db() -> Iterator[Session]:
        yield sqlite_session

    app.dependency_overrides[get_db] = override_db
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.05)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
