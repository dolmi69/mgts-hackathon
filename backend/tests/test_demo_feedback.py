from uuid import uuid4

from app.config import settings
from app.services import analysis
from app.services.rules import recompute_flags

ENDPOINT = "/api/demo/feedback"


def make_feedback(comment: str, score: int = 5) -> dict:
    return {"submission_id": str(uuid4()), "score": score, "comment": comment}


def test_feedback_runs_analysis_once_and_keeps_demo_out_of_program(client, db, monkeypatch):
    monkeypatch.setattr(settings, "demo_feedback_enabled", True)
    payload = make_feedback("Задач слишком много, приходится постоянно перерабатывать.")

    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 202
    receipt = response.json()
    assert receipt["analysis_status"] == "pending"

    result = client.get(f"{ENDPOINT}/{payload['submission_id']}").json()
    assert result["analysis_status"] == "done"
    assert result["model"] == "mock"
    assert result["signal_codes"] == ["R1"]
    assert client.post(ENDPOINT, json=payload).json() == result
    assert client.post(ENDPOINT, json={**payload, "score": 4}).status_code == 409

    recompute_flags(db)
    card = client.get(f"/api/interns/{receipt['intern_id']}").json()
    assert len(card["answers"]) == 1
    assert card["answers"][0]["comment"] == payload["comment"]
    assert card["flags"][0]["evidence_answer_ids"] == [receipt["answer_id"]]
    assert client.get("/api/periods").json() == []
    summary = client.get("/api/dashboard/summary").json()
    assert summary["demo_feedback_enabled"] is True
    assert summary["kpis"]["interns"] == summary["kpis"]["flags_medium"] == 0


def test_demo_feedback_is_opt_in_and_validates_input(client, monkeypatch):
    payload = make_feedback("Задачи понятные, всё успеваю.")
    assert client.post(ENDPOINT, json=payload).status_code == 404

    monkeypatch.setattr(settings, "demo_feedback_enabled", True)
    assert client.post(ENDPOINT, json={**payload, "comment": "  "}).status_code == 422
    assert client.post(ENDPOINT, json={**payload, "score": 6}).status_code == 422
    assert client.get(f"{ENDPOINT}/{payload['submission_id']}").status_code == 404


def test_demo_keeps_answer_on_provider_failure(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_feedback_enabled", True)

    def unavailable():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(analysis, "get_provider", unavailable)
    payload = make_feedback("Нужна помощь с задачами.")
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 202
    result = client.get(f"{ENDPOINT}/{payload['submission_id']}").json()
    assert result["analysis_status"] == "failed"
    assert result["signal_codes"] == []
    card = client.get(f"/api/interns/{result['intern_id']}").json()
    assert card["answers"][0]["comment"] == payload["comment"]
