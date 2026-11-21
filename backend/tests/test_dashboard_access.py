from uuid import uuid4

import pytest

from app.auth import validate_deployment_auth
from app.config import settings


def test_read_only_account_cannot_submit_demo_or_trigger_analysis(client, monkeypatch):
    monkeypatch.setattr(settings, "dashboard_user", "hr")
    monkeypatch.setattr(settings, "dashboard_password", "test-hr-password")
    monkeypatch.setattr(settings, "dashboard_viewer_user", "viewer")
    monkeypatch.setattr(settings, "dashboard_viewer_password", "test-viewer-password")
    monkeypatch.setattr(settings, "demo_feedback_enabled", True)

    hr = ("hr", "test-hr-password")
    viewer = ("viewer", "test-viewer-password")
    survey = {"submission_id": str(uuid4()), "score": 5, "comment": "Всё понятно и интересно."}
    assert client.get("/api/dashboard/summary").status_code == 401
    assert client.get("/api/dashboard/summary", auth=viewer).status_code == 200
    assert client.post("/api/demo/feedback", auth=viewer, json=survey).status_code == 403
    response = client.post("/api/demo/feedback", auth=hr, json=survey)
    assert response.status_code == 202
    assert client.post(
        f"/api/interns/{response.json()['intern_id']}/reanalyze", auth=viewer
    ).status_code == 403


def test_pilot_requires_auth_and_separate_demo(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "pilot")
    with pytest.raises(RuntimeError, match="unique webhook secret"):
        validate_deployment_auth()

    monkeypatch.setattr(settings, "dashboard_user", "hr")
    monkeypatch.setattr(settings, "dashboard_password", "test-hr-password")
    monkeypatch.setattr(settings, "webhook_secret", "test-webhook-secret")
    monkeypatch.setattr(settings, "seed_on_startup", True)
    with pytest.raises(RuntimeError, match="separate demo environment"):
        validate_deployment_auth()
    monkeypatch.setattr(settings, "seed_on_startup", False)
    validate_deployment_auth()
