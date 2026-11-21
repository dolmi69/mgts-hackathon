from __future__ import annotations

import atexit
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema


def _test_database_url() -> str:
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        temporary_db = TemporaryDirectory(prefix="mgts-pytest-")
        atexit.register(temporary_db.cleanup)
        return f"sqlite:///{Path(temporary_db.name) / 'app.db'}"

    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("TEST_DATABASE_URL must point to a PostgreSQL test database")
    schema = f"mgts_test_{uuid4().hex}"
    administrator = create_engine(url)
    with administrator.begin() as connection:
        connection.execute(CreateSchema(schema))

    def cleanup() -> None:
        with administrator.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        administrator.dispose()

    atexit.register(cleanup)
    options = f"{url.query.get('options', '')} -csearch_path={schema}".strip()
    return url.update_query_dict({"options": options}).render_as_string(hide_password=False)

# Must be set before importing the application.
os.environ["DATABASE_URL"] = _test_database_url()
os.environ["APP_ENV"] = "dev"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["SEED_ANALYZE"] = "false"
os.environ["ANALYZE_IN_BACKGROUND"] = "false"
os.environ["DEMO_FEEDBACK_ENABLED"] = "false"
os.environ["WEBHOOK_SECRET"] = ""
os.environ["DASHBOARD_USER"] = ""
os.environ["DASHBOARD_PASSWORD"] = ""
os.environ["DASHBOARD_VIEWER_USER"] = ""
os.environ["DASHBOARD_VIEWER_PASSWORD"] = ""

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
