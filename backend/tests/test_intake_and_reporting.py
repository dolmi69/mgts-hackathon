from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

from app.config import settings
from app.database import SessionLocal
from app.models import AIAnalysis, Intern
from app.schemas import CanonicalAnswer
from app.services.ingest import upsert_answer
from app.services.rules import recompute_flags
from app.services.survey import last_closed_period, survey_deadline


def _yandex_payload() -> dict:
    return {
        "answer_id": "provider-001",
        "form_id": "form-001",
        "intern_id": "int-001",
        "period": "2025-10",
        "created_at": "2025-10-16T12:00:00+03:00",
        "Оценка нагрузки": "2",
        "Общая оценка": "5",
    }


def test_yandex_picks_overall_and_refuses_ambiguous_answers(client, monkeypatch):
    payload = _yandex_payload()
    result = client.post("/api/webhooks/yandex-forms", json=payload).json()
    assert result["created"]
    assert client.get("/api/dashboard/summary?period=2025-10").json()["kpis"]["avg_score"] == 5
    repeated = client.post("/api/webhooks/yandex", json=payload).json()
    assert repeated["created"] is False and repeated["answer_id"] == result["answer_id"]

    ambiguous = {
        **payload,
        "answer_id": "ambiguous",
        "answers": [
            {"id": "q-overall", "label": "Общая оценка", "value": "4"},
            {"id": "q-conflict", "label": "Общая оценка", "value": "5"},
        ],
    }
    unparsed = client.post("/api/webhooks/yandex_forms", json=ambiguous).json()
    assert unparsed["status"] == "stored_unparsed"
    missing = {k: v for k, v in {**payload, "answer_id": "missing"}.items() if k != "Общая оценка"}
    assert client.post("/api/webhooks/yandex_forms", json=missing).json()["status"] == "stored_unparsed"
    monkeypatch.setattr(settings, "yandex_forms_fields", {"form-001": {"overall": "q-overall"}})
    assert client.post("/api/webhooks/yandex_forms", json=ambiguous).json()["created"]
    assert client.get("/api/dashboard/summary?period=2025-10").json()["kpis"]["avg_score"] == 4


def test_webhook_does_not_echo_invalid_input_or_accept_demo_namespace(client, caplog):
    payload = {
        "external_answer_id": "source-42",
        "period": "2025-10",
        "respondent_type": "intern",
        "intern_external_id": "int-001",
        "scores": {"overall": 5},
        "submitted_at": "fake.private@example.test",
    }
    response = client.post("/api/webhooks/generic", json=payload)
    assert response.json()["status"] == "stored_unparsed"
    assert "submitted_at" in response.json()["message"]
    assert payload["submitted_at"] not in response.text and payload["submitted_at"] not in caplog.text
    payload.pop("submitted_at")
    assert client.post("/api/webhooks/DEMO-FEEDBACK", json=payload).status_code == 422
    payload["intern_external_id"] = "demo-feedback-internal"
    assert client.post("/api/webhooks/generic", json=payload).status_code == 422


def test_csv_fallback_deduplicates_forms_but_preserves_new_submissions(client):
    payload = _yandex_payload()
    result = client.post("/api/webhooks/yandex_forms", json=payload).json()
    assert result["created"]
    answers = (
        "external_answer_id;form_id;period;respondent_type;intern_external_id;overall;submitted_at\n"
        "provider-001;form-001;2025-10;intern;int-001;5;2025-10-16T12:00:00+03:00\n"
        "person-two;;2025-10;intern;int-002;2;2025-10-16T12:00:00+03:00\n"
        "older;;2025-10;intern;int-001;1;2025-10-15T12:00:00+03:00\n"
        "mentor-only;;2025-10;mentor;int-003;4;2025-10-16T12:00:00+03:00\n"
    )
    result = client.post("/api/import/csv", files={"answers_file": ("answers.csv", answers)}).json()
    assert result["imported"] == 3
    assert result["duplicates"] == 1

    summary = client.get("/api/dashboard/summary?period=2025-10").json()
    assert summary["kpis"]["avg_score"] == 3.5
    assert summary["kpis"]["interns"] == 3
    assert summary["kpis"]["responded"] == 2
    assert summary["kpis"]["mentor_responded"] == 1
    calendar = client.get("/api/dashboard/calendar?period=2025-10").json()
    assert calendar["totals"]["intern_responded"] == 2
    assert calendar["totals"]["mentor_responded"] == 1
    first = next(item for item in summary["interns"] if item["external_id"] == "int-001")
    card = client.get(f"/api/interns/{first['id']}").json()
    assert [(a["overall_score"], a["current_for_month"]) for a in card["answers"]] == [
        (5, True), (1, False)
    ]


def test_concurrent_forms_and_csv_intake_keeps_one_answer():
    barrier = Barrier(2)

    def submit(source: str) -> tuple[int, bool]:
        payload = CanonicalAnswer(
            source=source, external_answer_id="response-42", intern_external_id="new-intern",
            period=date(2025, 10, 1), respondent_type="intern", scores={"overall": 5},
        )
        with SessionLocal() as db:
            barrier.wait(timeout=5)
            answer, created = upsert_answer(db, payload)
            db.commit()
            return answer.id, created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("csv", "yandex_forms")))
    assert len({answer_id for answer_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_scheduled_months_and_r4_are_visible_with_no_responses(client, db):
    participant = Intern(external_id="p-1", name="Стажёр", direction="Разработка", start_period=date(2025, 10, 1))
    db.add(participant)
    db.commit()
    month = date(2025, 10, 1)
    assert survey_deadline(month, grace_days=3) == date(2025, 11, 3)
    assert last_closed_period(date(2025, 11, 3), grace_days=3) == date(2025, 9, 1)
    assert last_closed_period(date(2025, 11, 4), grace_days=3) == month
    assert recompute_flags(db, today=date(2025, 11, 3)) == 0
    assert recompute_flags(db, today=date(2025, 11, 4)) == 1

    assert "2025-10-01" in {item["period"] for item in client.get("/api/periods").json()}
    calendar = client.get("/api/dashboard/calendar?period=2025-10").json()
    assert calendar["deadline"] == "2025-11-03" and calendar["phase"] == "closed"
    assert calendar["totals"] == {
        "expected": 1, "intern_responded": 0, "mentor_responded": 0, "intern_missing": 1, "mentor_missing": 1
    }
    assert calendar["directions"][0]["direction"] == "Разработка"
    assert calendar["reminder_draft"]
    summary = client.get("/api/dashboard/summary?period=2025-10").json()
    assert summary["interns"][0]["flags"][0]["rule_code"] == "R4"


def test_topic_map_counts_a_topic_once_per_intern_and_hides_small_groups(client, db):
    for number, group in enumerate(("Разработка", "Разработка", "Разработка", "Дизайн", "Тестирование")):
        db.add(Intern(external_id=f"p-{number}", name=f"Стажёр {number}", direction=group))
        db.commit()
        result = client.post("/api/webhooks/generic", json={
            "external_answer_id": f"response-{number}",
            "period": "2025-10",
            "respondent_type": "intern",
            "intern_external_id": f"p-{number}",
            "scores": {"overall": 5},
            "comment": "Постоянно перегружен.",
        }).json()
        db.add(AIAnalysis(
            answer_id=result["answer_id"],
            model="mock",
            prompt_version="test",
            status="done",
            problem_topics=["workload", "workload"] if number == 0 else [],
        ))
        db.commit()

    result = client.get("/api/dashboard/topics?period=2025-10&months=2").json()
    assert [item["period"] for item in result["periods"]] == ["2025-09-01", "2025-10-01"]
    program, *groups = result["directions"]
    assert program["periods"][1]["responded"] == 5
    teams = {group["direction"]: group["periods"][1] for group in groups}
    assert teams["Разработка"]["topics"] == [{"topic": "workload", "count": 1}]
    assert teams["Дизайн"] == {
        "period": "2025-10-01", "responded": None, "suppressed": True, "topics": []
    }
