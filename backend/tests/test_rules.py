from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import AIAnalysis, Answer, Intern, Mentor
from app.services.rules import evaluate_intern, load_rules_config


def make_intern(db: Session, *, start: date = date(2025, 10, 1)) -> tuple[Intern, Mentor]:
    mentor = Mentor(external_id="men-test", name="Тестовый Наставник")
    db.add(mentor)
    db.flush()
    intern = Intern(
        external_id="int-test",
        name="Тестовый Стажёр",
        direction="Тест",
        mentor_id=mentor.id,
        start_period=start,
    )
    db.add(intern)
    db.commit()
    db.refresh(intern)
    return intern, mentor


def make_answer(
    db: Session,
    intern: Intern,
    mentor: Mentor,
    period: date,
    *,
    role: str = "intern",
    score: float,
    sentiment: str | None = None,
    topics: list[str] | None = None,
    problem_topics: list[str] | None = None,
    comment: str = "",
) -> Answer:
    answer = Answer(
        source="test",
        external_answer_id=f"{intern.external_id}-{period.isoformat()}-{role}-{score}",
        period=period,
        respondent_type=role,
        respondent_external_id=intern.external_id if role == "intern" else mentor.external_id,
        intern_id=intern.id,
        mentor_id=mentor.id,
        overall_score=score,
        scores={"overall": score},
        comment=comment or "Тестовый комментарий.",
        analysis_status="pending",
    )
    db.add(answer)
    db.flush()
    if sentiment is not None:
        answer.analysis = AIAnalysis(
            answer_id=answer.id,
            model="mock",
            prompt_version="v1",
            sentiment=sentiment,
            topics=topics or [],
            problem_topics=problem_topics or [],
            summary="Тестовое резюме.",
            evidence=[comment] if comment else [],
            confidence=0.8,
            status="done",
        )
        answer.analysis_status = "done"
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def test_r1_high_score_with_complaint(db):
    intern, mentor = make_intern(db)
    answer = make_answer(
        db,
        intern,
        mentor,
        date(2025, 10, 1),
        score=5,
        sentiment="mixed",
        topics=["workload"],
        problem_topics=["workload"],
        comment="Всё нравится, но нагрузка выросла.",
    )
    flags = evaluate_intern(intern, [answer], date(2025, 10, 1), load_rules_config())
    assert any(flag["rule_code"] == "R1" for flag in flags)


def test_r1_does_not_fire_on_positive_comment(db):
    intern, mentor = make_intern(db)
    answer = make_answer(
        db,
        intern,
        mentor,
        date(2025, 10, 1),
        score=5,
        sentiment="positive",
        topics=["tasks"],
        problem_topics=[],
        comment="Интересные задачи, всё нравится.",
    )
    flags = evaluate_intern(intern, [answer], date(2025, 10, 1), load_rules_config())
    assert not any(flag["rule_code"] == "R1" for flag in flags)


def test_r2_intern_and_mentor_disagree(db):
    intern, mentor = make_intern(db)
    intern_answer = make_answer(
        db,
        intern,
        mentor,
        date(2025, 10, 1),
        score=5,
        sentiment="positive",
        topics=["tasks"],
        comment="Всё отлично, задачи нравятся.",
    )
    mentor_answer = make_answer(
        db,
        intern,
        mentor,
        date(2025, 10, 1),
        role="mentor",
        score=3,
        sentiment="negative",
        topics=["mentorship"],
        problem_topics=["mentorship"],
        comment="Не хватает самостоятельности.",
    )
    flags = evaluate_intern(
        intern, [intern_answer, mentor_answer], date(2025, 10, 1), load_rules_config()
    )
    r2 = next(flag for flag in flags if flag["rule_code"] == "R2")
    assert r2["severity"] == "high"
    assert len(r2["evidence_answer_ids"]) == 2


def test_r3_declining_scores(db):
    intern, mentor = make_intern(db)
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=5),
        make_answer(db, intern, mentor, date(2025, 11, 1), score=4),
        make_answer(db, intern, mentor, date(2025, 12, 1), score=2),
    ]
    flags = evaluate_intern(intern, answers, date(2025, 12, 1), load_rules_config())
    r3 = next(flag for flag in flags if flag["rule_code"] == "R3")
    assert r3["severity"] == "high"


def test_r3_does_not_fire_on_one_bad_month(db):
    intern, mentor = make_intern(db)
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=4),
        make_answer(db, intern, mentor, date(2025, 11, 1), score=3),
        make_answer(db, intern, mentor, date(2025, 12, 1), score=4),
    ]
    flags = evaluate_intern(intern, answers, date(2025, 12, 1), load_rules_config())
    assert not any(flag["rule_code"] == "R3" for flag in flags)


def test_r3_does_not_fire_on_single_small_dip(db):
    intern, mentor = make_intern(db)
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=5),
        make_answer(db, intern, mentor, date(2025, 11, 1), score=5),
        make_answer(db, intern, mentor, date(2025, 12, 1), score=4),
        make_answer(db, intern, mentor, date(2026, 1, 1), score=4),
    ]
    flags = evaluate_intern(intern, answers, date(2026, 1, 1), load_rules_config())
    assert not any(flag["rule_code"] == "R3" for flag in flags)


def test_r3_fires_on_sustained_decline(db):
    intern, mentor = make_intern(db)
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=5),
        make_answer(db, intern, mentor, date(2025, 11, 1), score=5),
        make_answer(db, intern, mentor, date(2025, 12, 1), score=4),
        make_answer(db, intern, mentor, date(2026, 1, 1), score=4),
        make_answer(db, intern, mentor, date(2026, 2, 1), score=3),
        make_answer(db, intern, mentor, date(2026, 3, 1), score=3),
    ]
    flags = evaluate_intern(intern, answers, date(2026, 3, 1), load_rules_config())
    r3 = next(flag for flag in flags if flag["rule_code"] == "R3")
    assert r3["severity"] == "high"
    assert len(r3["evidence_answer_ids"]) == 6


def test_r4_missing_surveys(db):
    intern, mentor = make_intern(db, start=date(2025, 10, 1))
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=4),
        make_answer(db, intern, mentor, date(2025, 11, 1), score=4),
        make_answer(db, intern, mentor, date(2025, 12, 1), score=4),
        make_answer(db, intern, mentor, date(2026, 1, 1), score=4),
    ]
    flags = evaluate_intern(intern, answers, date(2026, 3, 1), load_rules_config())
    r4_flags = [flag for flag in flags if flag["rule_code"] == "R4"]
    assert {flag["period"] for flag in r4_flags} == {date(2026, 2, 1), date(2026, 3, 1)}
    assert any(flag["severity"] == "high" for flag in r4_flags)


def test_r5_repeating_problem(db):
    intern, mentor = make_intern(db)
    answers = [
        make_answer(
            db,
            intern,
            mentor,
            date(2025, 10, 1),
            score=3,
            sentiment="negative",
            topics=["workload"],
            problem_topics=["workload"],
            comment="Слишком много задач.",
        ),
        make_answer(
            db,
            intern,
            mentor,
            date(2025, 11, 1),
            score=3,
            sentiment="negative",
            topics=["workload"],
            problem_topics=["workload"],
            comment="Нагрузка не снижается.",
        ),
        make_answer(
            db,
            intern,
            mentor,
            date(2025, 12, 1),
            score=3,
            sentiment="negative",
            topics=["workload"],
            problem_topics=["workload"],
            comment="Снова слишком много задач.",
        ),
    ]
    flags = evaluate_intern(intern, answers, date(2025, 12, 1), load_rules_config())
    assert any(flag["rule_code"] == "R5" for flag in flags)


def test_r3_uses_calendar_months_not_answer_order(db):
    # Three answers spread over nine months: −0.25 a month is not a trend.
    intern, mentor = make_intern(db)
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=5),
        make_answer(db, intern, mentor, date(2026, 2, 1), score=4),
        make_answer(db, intern, mentor, date(2026, 6, 1), score=3),
    ]
    flags = evaluate_intern(intern, answers, date(2026, 6, 1), load_rules_config())
    assert not [flag for flag in flags if flag["rule_code"] == "R3"]


def test_r3_window_is_six_calendar_months(db):
    intern, mentor = make_intern(db)
    answers = [
        make_answer(db, intern, mentor, date(2025, 10, 1), score=5),
        make_answer(db, intern, mentor, date(2025, 11, 1), score=4),
        make_answer(db, intern, mentor, date(2025, 12, 1), score=2),
    ]
    # A year later the old decline is outside the window.
    late = make_answer(db, intern, mentor, date(2026, 12, 1), score=2)
    flags = evaluate_intern(intern, [*answers, late], date(2026, 12, 1), load_rules_config())
    assert not [flag for flag in flags if flag["rule_code"] == "R3" and flag["period"] == date(2026, 12, 1)]


def test_r4_flags_intern_without_any_answer(db):
    intern, _mentor = make_intern(db, start=date(2026, 1, 1))
    flags = evaluate_intern(intern, [], date(2026, 3, 1), load_rules_config())
    r4 = [flag for flag in flags if flag["rule_code"] == "R4"]
    assert {flag["period"] for flag in r4} == {date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)}
    assert max(r4, key=lambda flag: flag["period"])["severity"] == "high"


def test_r6_low_score(db):
    intern, mentor = make_intern(db)
    low = make_answer(db, intern, mentor, date(2025, 10, 1), score=1)
    borderline = make_answer(db, intern, mentor, date(2025, 10, 1), role="mentor", score=2)
    flags = evaluate_intern(intern, [low, borderline], date(2025, 10, 1), load_rules_config())
    r6 = next(flag for flag in flags if flag["rule_code"] == "R6")
    assert r6["severity"] == "high"
    assert set(r6["evidence_answer_ids"]) == {low.id, borderline.id}


def test_r6_can_be_disabled(db):
    intern, mentor = make_intern(db)
    low = make_answer(db, intern, mentor, date(2025, 10, 1), score=1)
    config = load_rules_config()
    config["r6"]["enabled"] = False
    flags = evaluate_intern(intern, [low], date(2025, 10, 1), config)
    assert not [flag for flag in flags if flag["rule_code"] == "R6"]


def test_r7_intent_to_leave(db):
    intern, mentor = make_intern(db)
    comment = "Думаю о выходе, ищу другую работу."
    answer = make_answer(
        db, intern, mentor, date(2025, 10, 1), score=3, sentiment="negative",
        topics=["leave"], problem_topics=["leave"], comment=comment,
    )
    flags = evaluate_intern(intern, [answer], date(2025, 10, 1), load_rules_config())
    r7 = next(flag for flag in flags if flag["rule_code"] == "R7")
    assert r7["severity"] == "high"
    assert r7["title"] == "Стажёр говорит о намерении уйти"
    assert comment.rstrip(".") in r7["explanation"]
    assert r7["explanation"].startswith("Стажёр пишет")
    assert r7["evidence_answer_ids"] == [answer.id]


def test_r7_from_mentor_comment(db):
    intern, mentor = make_intern(db)
    answer = make_answer(
        db, intern, mentor, date(2025, 10, 1), role="mentor", score=3, sentiment="negative",
        topics=["leave"], problem_topics=["leave"], comment="Стажёр сказал, что хочет уйти.",
    )
    flags = evaluate_intern(intern, [answer], date(2025, 10, 1), load_rules_config())
    r7 = next(flag for flag in flags if flag["rule_code"] == "R7")
    assert r7["explanation"].startswith("Наставник пишет")
    assert r7["evidence_answer_ids"] == [answer.id]


def test_r7_ignores_topic_without_complaint(db):
    intern, mentor = make_intern(db)
    answer = make_answer(
        db, intern, mentor, date(2025, 10, 1), score=5, sentiment="positive",
        topics=["team"], problem_topics=[], comment="Уходить не собираюсь, всё нравится.",
    )
    flags = evaluate_intern(intern, [answer], date(2025, 10, 1), load_rules_config())
    assert not [flag for flag in flags if flag["rule_code"] == "R7"]


def test_r7_can_be_disabled(db):
    intern, mentor = make_intern(db)
    answer = make_answer(
        db, intern, mentor, date(2025, 10, 1), score=3, sentiment="negative",
        topics=["leave"], problem_topics=["leave"], comment="Хочу уволиться.",
    )
    config = load_rules_config()
    assert config["r7"]["enabled"] is True
    config["r7"]["enabled"] = False
    flags = evaluate_intern(intern, [answer], date(2025, 10, 1), config)
    assert not [flag for flag in flags if flag["rule_code"] == "R7"]
