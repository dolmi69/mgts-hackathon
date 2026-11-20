from __future__ import annotations


def _payload(answer_id: str = "test-1") -> dict:
    return {
        "external_answer_id": answer_id,
        "period": "2025-10-01",
        "respondent_type": "intern",
        "intern_external_id": "int-api",
        "mentor_external_id": "men-api",
        "scores": {"overall": 5, "workload": 3, "mentorship": 5},
        "comment": "Всё нравится, но задач стало слишком много.",
        "submitted_at": "2025-10-28T18:00:00+03:00",
    }


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["db"] == "ok"


def test_webhook_is_idempotent_and_card_is_available(client):
    first = client.post("/api/webhooks/generic", json=_payload())
    assert first.status_code == 202
    assert first.json()["created"] is True
    answer_id = first.json()["answer_id"]

    second = client.post("/api/webhooks/generic", json=_payload())
    assert second.status_code == 202
    assert second.json()["created"] is False
    assert second.json()["answer_id"] == answer_id

    periods = client.get("/api/periods")
    assert periods.status_code == 200
    assert periods.json()[0]["period"] == "2025-10-01"

    summary = client.get("/api/dashboard/summary")
    assert summary.status_code == 200
    data = summary.json()
    assert data["kpis"]["interns"] == 1
    assert data["kpis"]["responded"] == 1
    assert data["interns"][0]["name"] == "int-api"

    intern_id = data["interns"][0]["id"]
    card = client.get(f"/api/interns/{intern_id}")
    assert card.status_code == 200
    body = card.json()
    assert len(body["answers"]) == 1
    assert body["answers"][0]["analysis_status"] == "pending"
    assert body["stats"]["total_answers"] == 1


def test_invalid_webhook_is_stored_not_crashing(client):
    response = client.post("/api/webhooks/generic", json={"foo": "bar"})
    assert response.status_code == 202
    assert response.json()["status"] == "stored_unparsed"


def test_csv_import_and_duplicate(client):
    interns_csv = (
        "external_id;name;direction;mentor_external_id;start_period\r\n"
        "int-csv;Тестовый Стажёр;Аналитика;men-csv;2025-10\r\n"
    ).encode("utf-8-sig")
    answers_csv = (
        "external_answer_id;period;respondent_type;intern_external_id;"
        "mentor_external_id;overall;comment\r\n"
        "csv-1;2025-11;intern;int-csv;men-csv;4;Всё хорошо, задачи понятные.\r\n"
    ).encode("utf-8-sig")

    response = client.post(
        "/api/import/csv",
        files={
            "interns_file": ("interns.csv", interns_csv, "text/csv"),
            "answers_file": ("answers.csv", answers_csv, "text/csv"),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 0
    assert body["errors"] == []

    duplicate = client.post(
        "/api/import/csv",
        files={"answers_file": ("answers.csv", answers_csv, "text/csv")},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicates"] == 1

    summary = client.get("/api/dashboard/summary?period=2025-11")
    assert summary.status_code == 200
    assert summary.json()["interns"][0]["name"] == "Тестовый Стажёр"
