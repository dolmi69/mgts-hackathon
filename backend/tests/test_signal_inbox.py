from datetime import date
from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.models import Answer, RiskFlag
from app.schemas import CanonicalAnswer
from app.services.ingest import upsert_answer
from app.services.rules import recompute_flags, replace_demo_flags


def save_answer(db, intern_id, month, score, *, role="intern"):
    answer, _ = upsert_answer(db, CanonicalAnswer(
        external_answer_id=f"{intern_id}-{month}-{role}",
        intern_external_id=intern_id,
        period=month,
        respondent_type=role,
        scores={"overall": score},
    ))
    answer.analysis_status = "done"
    db.commit()
    return answer


def test_review_history_survives_recompute_and_inactive_flags_leave_dashboard(client, db):
    answer = save_answer(db, "hr-1", "2026-01", 2)
    save_answer(db, "hr-1", "2026-02", 4)
    recompute_flags(db, today=date(2026, 3, 5))

    inbox = client.get("/api/signals", params={"state": "new", "period": "2026-01"}).json()
    signal, = inbox["items"]
    assert signal["rule_code"] == "R6"
    assert signal["evidence_answer_ids"] == [answer.id]
    assert signal["history"] == []
    action_url = f"/api/signals/{signal['id']}/actions"
    review = {"state": "reviewed", "note": "Стажёру добавили время на задачу."}
    assert client.post(action_url, json=review).status_code == 409

    take = {"state": "in_progress", "assigned_to": "Ирина", "note": "Уточнить нагрузку."}
    taken = client.post(action_url, json=take).json()
    assert taken["assigned_to"] == "Ирина"
    assert taken["state"] == "in_progress"
    assert taken["history"][0]["actor"] == "local-hr"
    assert taken["next_survey"]["outcome"] == "not_repeated"
    assert client.post(action_url, json=take).json() == taken
    assert client.post(action_url, json={**take, "assigned_to": "Олег"}).status_code == 409

    reviewed = client.post(action_url, json=review).json()
    assert reviewed["state"] == "reviewed"
    assert [entry["note"] for entry in reviewed["history"]] == [take["note"], review["note"]]
    recompute_flags(db, today=date(2026, 3, 5))
    assert client.get("/api/signals", params={"state": "reviewed"}).json()["items"] == [reviewed]

    answer.overall_score = 5
    db.commit()
    recompute_flags(db, today=date(2026, 3, 5))
    archived, = client.get("/api/signals", params={"state": "reviewed"}).json()["items"]
    assert archived["id"] == signal["id"]
    assert not archived["active"]
    assert archived["history"] == reviewed["history"]
    assert client.get("/api/dashboard/summary?period=2026-01").json()["kpis"]["flags_medium"] == 0


def test_inbox_keeps_paired_demo_flags_out_of_program(client, db, monkeypatch):
    monkeypatch.setattr(settings, "demo_feedback_enabled", True)
    intern = client.post("/api/demo/feedback", json={
        "submission_id": str(uuid4()), "period": "2026-03", "respondent_type": "intern",
        "score": 5, "comment": "Задачи понятные.",
    }).json()
    mentor = client.post("/api/demo/feedback", json={
        "submission_id": str(uuid4()), "scenario_id": intern["scenario_id"],
        "period": "2026-03", "respondent_type": "mentor", "score": 3,
        "comment": "Нужно больше времени на разбор задач.",
    }).json()
    assert client.get("/api/signals").json()["items"] == []
    demo_item, = client.get("/api/signals?scope=demo&severity=high").json()["items"]
    assert demo_item["rule_code"] == "R2"
    assert demo_item["evidence_answer_ids"] == [intern["answer_id"], mentor["answer_id"]]
    first_id = demo_item["id"]
    replace_demo_flags(db, db.get(Answer, intern["answer_id"]))
    db.commit()
    assert db.scalar(select(RiskFlag.id).where(RiskFlag.rule_code == "R2")) == first_id

    take = client.post(f"/api/signals/{first_id}/actions", json={"state": "in_progress"})
    assert take.status_code == 200
    assert take.json()["next_survey"] is None

    april = {"scenario_id": intern["scenario_id"], "period": "2026-04"}
    assert client.post("/api/demo/feedback", json={
        **april, "submission_id": str(uuid4()), "respondent_type": "mentor",
        "score": 3, "comment": "Пока вижу сложности со сроками.",
    }).status_code == 202
    march_query = {
        "scope": "demo", "intern_id": intern["intern_id"], "period": "2026-03", "state": "in_progress",
    }
    pending, = client.get("/api/signals", params=march_query).json()["items"]
    assert pending["next_survey"]["outcome"] == "waiting"

    assert client.post("/api/demo/feedback", json={
        **april, "submission_id": str(uuid4()), "respondent_type": "intern",
        "score": 5, "comment": "Замечаний по работе нет.",
    }).status_code == 202
    repeated, = client.get("/api/signals", params=march_query).json()["items"]
    assert repeated["next_survey"]["outcome"] == "repeated"


def test_read_only_account_can_open_inbox_but_cannot_review(client, db, monkeypatch):
    save_answer(db, "hr-2", "2026-01", score=2)
    recompute_flags(db, today=date(2026, 2, 5))
    monkeypatch.setattr(settings, "dashboard_user", "hr")
    monkeypatch.setattr(settings, "dashboard_password", "test-hr")
    monkeypatch.setattr(settings, "dashboard_viewer_user", "viewer")
    monkeypatch.setattr(settings, "dashboard_viewer_password", "test-viewer")

    viewer = ("viewer", "test-viewer")
    assert client.get("/api/signals").status_code == 401
    inbox = client.get("/api/signals", auth=viewer).json()
    signal, = inbox["items"]
    assert not inbox["can_write"]
    action_url = f"/api/signals/{signal['id']}/actions"
    take = {"state": "in_progress"}
    assert client.post(action_url, auth=viewer, json=take).status_code == 403
    accepted = client.post(action_url, auth=("hr", "test-hr"), json=take)
    assert accepted.status_code == 200
    assert accepted.json()["history"][0]["actor"] == "hr"
