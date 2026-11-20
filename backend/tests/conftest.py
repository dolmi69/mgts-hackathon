from __future__ import annotations

import os
from pathlib import Path

# Must be set before importing the application.
os.environ["DATABASE_URL"] = "sqlite:///./test_app.db"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["SEED_ANALYZE"] = "false"
os.environ["ANALYZE_IN_BACKGROUND"] = "false"
os.environ["WEBHOOK_SECRET"] = ""
os.environ["DASHBOARD_USER"] = ""
os.environ["DASHBOARD_PASSWORD"] = ""

import pytest
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
