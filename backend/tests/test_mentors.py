from __future__ import annotations

from datetime import date

from app.models import Intern, Mentor, RiskFlag
from app.seed_data import seed_database
from app.services.rules import DEFAULT_RULES, load_rules_config


def _flag(intern: Intern, severity: str, code: str = "R1") -> RiskFlag:
    return RiskFlag(
        intern_id=intern.id,
        period=date(2026, 3, 1),
        rule_code=code,
        severity=severity,
        title="Сигнал",
        explanation="Тестовый сигнал",
        status="open",
    )


def test_mentor_thresholds_have_defaults():
    assert DEFAULT_RULES["mentors"] == {"attention_share": 0.75, "min_interns": 2}
    assert load_rules_config()["mentors"]["min_interns"] == 2


def test_mentor_with_all_interns_flagged_needs_attention(client, db):
    busy = Mentor(external_id="mt-a", name="Наставник А")
    calm = Mentor(external_id="mt-b", name="Наставник Б")
    solo = Mentor(external_id="mt-c", name="Наставник В")
    db.add_all([busy, calm, solo])
    db.flush()
    start = date(2026, 3, 1)
    interns = [
        Intern(external_id=f"m-{index}", name=f"Стажёр {index}", mentor_id=mentor.id, start_period=start)
        for index, mentor in enumerate([busy, busy, busy, calm, calm, solo])
    ]
    db.add_all(interns)
    db.flush()
    db.add_all([
        _flag(interns[0], "high"),
        _flag(interns[0], "low", "R4"),
        _flag(interns[1], "medium"),
        _flag(interns[2], "high"),
        _flag(interns[3], "low"),
        _flag(interns[5], "high"),
    ])
    db.commit()

    mentors = client.get("/api/dashboard/summary?period=2026-03").json()["mentors"]
    assert [item["name"] for item in mentors] == ["Наставник А", "Наставник В", "Наставник Б"]
    by_name = {item["name"]: item for item in mentors}

    busy_row = by_name["Наставник А"]
    assert busy_row["interns_total"] == 3
    assert busy_row["interns_with_flags"] == 3
    assert busy_row["interns_with_high"] == 2
    assert busy_row["share_with_flags"] == 1.0
    assert busy_row["flagged_intern_ids"] == sorted(intern.id for intern in interns[:3])
    assert busy_row["needs_attention"] is True

    calm_row = by_name["Наставник Б"]
    assert (calm_row["interns_with_flags"], calm_row["share_with_flags"]) == (1, 0.5)
    assert calm_row["needs_attention"] is False
    # One intern is too small a group to call it a pattern.
    assert by_name["Наставник В"]["needs_attention"] is False


def test_seed_mentor_with_all_interns_flagged(client, db):
    seed_database(db, analyze=True, reset=True)
    summary = client.get("/api/dashboard/summary?period=2026-03").json()
    mentors = {item["name"]: item for item in summary["mentors"]}
    oleg = mentors["Олег Кузнецов"]
    assert summary["mentors"][0]["name"] == "Олег Кузнецов"
    assert oleg["interns_total"] == 4 and oleg["interns_with_flags"] == 4
    assert oleg["needs_attention"] is True
    assert oleg["mentor_responded"] <= oleg["interns_total"]
    assert sum(item["interns_total"] for item in summary["mentors"]) == sum(
        1 for intern in summary["interns"] if intern["mentor_name"]
    )
    assert [item["name"] for item in summary["mentors"] if item["needs_attention"]] == ["Олег Кузнецов"]
