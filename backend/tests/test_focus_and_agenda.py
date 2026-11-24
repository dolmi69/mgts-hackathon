import re
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.database import engine
from app.models import Intern, Mentor, RiskFlag, SignalAction, utcnow
from app.services.agenda import MAX_AGENDA_ITEMS, build_agenda, plain_reason, signal_wait
from app.services.rules import DEFAULT_RULES, load_rules_config

MONTH = date(2026, 1, 1)
NOW = datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc)
SLA = {"high_days": 3, "medium_days": 7}
ALL_CODES = ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]


def make_flag(code, severity="high", explanation="", *, status="open", created_at=NOW, period=MONTH, **extra):
    return RiskFlag(
        rule_code=code, severity=severity, title="Сигнал", explanation=explanation,
        status=status, created_at=created_at, period=period, evidence_answer_ids=[], **extra,
    )


def add_intern(db, name, mentor=None):
    intern = Intern(
        external_id=f"ext-{name}", name=name, start_period=MONTH, status="active", mentor=mentor,
    )
    db.add(intern)
    db.flush()
    return intern


def add_flag(db, intern, code, severity, *, days=0, state=None, period=MONTH, status="open"):
    flag = make_flag(
        code, severity, status=status, period=period,
        created_at=utcnow() - timedelta(days=days, minutes=1), intern_id=intern.id,
    )
    db.add(flag)
    db.flush()
    for step in {"in_progress": ["in_progress"], "reviewed": ["in_progress", "reviewed"]}.get(state, []):
        db.add(SignalAction(flag_id=flag.id, state=step, actor="hr", assigned_to="hr"))
        db.flush()
    return flag


def get_focus(client):
    return client.get("/api/dashboard/summary?period=2026-01").json()["focus"]


# --- agenda -----------------------------------------------------------------


def agenda_variants():
    topics = [
        "нагрузка", "задачи", "наставничество", "команда", "процессы", "развитие",
        "намерение уйти", "другое", "нечто неизвестное",
    ]
    return [
        *(make_flag(code) for code in ALL_CODES),
        make_flag("R99"),
        *(make_flag(code, explanation="Наставник поставил оценку 1 за январь 2026.") for code in ALL_CODES),
        *(make_flag(code, explanation="Стажёр поставил оценку 1 за январь 2026.") for code in ALL_CODES),
        make_flag("R3", explanation="Оценки наставника снижаются с января 2026 (5) до марта 2026 (2)."),
        *(
            make_flag("R5", explanation=f"Тема «{topic}» повторяется в 2 из 3 последних комментариев "
                                        "стажёра. Пример: «текст».")
            for topic in topics
        ),
    ]


@pytest.mark.parametrize("flag", agenda_variants(), ids=lambda flag: flag.rule_code)
def test_agenda_wording_is_neutral(flag):
    item, = build_agenda([flag])
    for text in (item.question, item.why, plain_reason(flag)):
        assert text
        assert not re.search(r"[RР]\d", text)
        lowered = text.lower()
        for word in ("уволится", "уволит", "виноват", "вина ", "уйдёт", "уйдет", "риск"):
            assert word not in lowered
    assert item.question.endswith("?")
    assert item.rule_code == flag.rule_code


def test_plain_reason_names_missing_survey():
    assert plain_reason(make_flag("R4")) == "Не ответил на опрос"
    assert len({plain_reason(make_flag(code)) for code in ALL_CODES}) == len(ALL_CODES)


def test_agenda_is_ordered_limited_and_deduplicated():
    severities = dict.fromkeys(ALL_CODES, "medium") | {"R6": "low", "R7": "high", "R3": "high"}
    flags = [make_flag(code, severity) for code, severity in severities.items()]
    agenda = build_agenda(reversed(flags))
    assert [item.rule_code for item in agenda] == ["R7", "R3", "R1", "R2", "R4"]
    assert len(agenda) == MAX_AGENDA_ITEMS

    repeated = build_agenda([make_flag("R4", "medium"), make_flag("R4", "high", period=date(2026, 2, 1))])
    assert len(repeated) == 1
    # A repeating «leave» topic asks the same careful question as R7.
    leave = make_flag("R5", "medium", "Тема «намерение уйти» повторяется в 2 из 3 последних комментариев стажёра. ")
    assert [item.rule_code for item in build_agenda([leave, make_flag("R7")])] == ["R7"]
    assert build_agenda([]) == []


def test_repeated_topic_question_depends_on_topic():
    def ask(explanation):
        item, = build_agenda([make_flag("R5", "medium", explanation)])
        return item

    def sentence(topic):
        return f"Тема «{topic}» повторяется в 2 из 3 последних комментариев стажёра. Пример: «пример»."

    generic = ask("Формулировка изменилась.").question
    workload = ask(sentence("нагрузка"))
    assert "нагрузк" in workload.question and "нагрузка" in workload.why
    assert "наставник" in ask(sentence("наставничество")).question
    assert ask(sentence("нечто неизвестное")).question == generic
    both = ask(f"{sentence('нагрузка')} {sentence('команда')}")
    assert both.question == generic
    assert "нагрузка, команда" in both.why
    # The intern's own words inside the quote do not choose the topic.
    quoted = sentence("нагрузка").replace(
        "«пример»", "«Тема «команда» повторяется в 2 из 3 последних комментариев стажёра.»"
    )
    assert ask(quoted).question == workload.question


def test_mentor_answer_is_not_attributed_to_intern():
    low, = build_agenda([make_flag("R6", explanation="Наставник поставил оценку 1 за январь 2026.")])
    own, = build_agenda([make_flag("R6", explanation="Стажёр поставил оценку 1 за январь 2026.")])
    assert "Наставник" in low.why and "Стажёр" not in low.why
    assert "Наставник" not in own.why
    gap, = build_agenda([make_flag("R2", explanation="Стажёр оценил месяц на 5, наставник — на 2.")])
    for question in (low.question, gap.question):
        assert "оцен" not in question.lower()  # does not reveal the mentor's score
    careful, = build_agenda([make_flag("R7", explanation="Наставник пишет о намерении уйти: «…».")])
    assert "уйти" not in careful.question and "ухо" not in careful.question


# --- waiting time -----------------------------------------------------------


@pytest.mark.parametrize("severity, limit", [("high", 3), ("medium", 7)])
def test_overdue_boundary(severity, limit):
    due = make_flag("R6", severity, created_at=NOW - timedelta(days=limit))
    assert signal_wait(due, SLA, now=NOW) == (limit, True)
    almost = make_flag("R6", severity, created_at=NOW - timedelta(days=limit) + timedelta(seconds=1))
    assert signal_wait(almost, SLA, now=NOW) == (limit - 1, False)


def test_wait_is_timezone_safe_and_never_negative():
    created = NOW - timedelta(days=3)
    moscow = created.astimezone(timezone(timedelta(hours=3)))
    for value in (created, created.replace(tzinfo=None), moscow):
        assert signal_wait(make_flag("R6", created_at=value), SLA, now=NOW) == (3, True)
    future = make_flag("R6", created_at=NOW + timedelta(hours=1))
    assert signal_wait(future, SLA, now=NOW) == (0, False)


def test_wait_applies_only_to_open_new_signals():
    old = NOW - timedelta(days=30)
    assert signal_wait(make_flag("R6", "low", created_at=old), SLA, now=NOW) == (30, False)
    assert signal_wait(make_flag("R6", status="inactive", created_at=old), SLA, now=NOW) == (None, False)
    taken = make_flag("R6", created_at=old)
    taken.actions.append(SignalAction(state="in_progress", actor="hr", assigned_to="hr"))
    assert signal_wait(taken, SLA, now=NOW) == (None, False)


def test_sla_settings():
    assert DEFAULT_RULES["sla"] == SLA
    assert load_rules_config()["sla"] == SLA


def test_signals_api_reports_waiting(client, db):
    intern = add_intern(db, "Анна")
    flag = add_flag(db, intern, "R6", "high", days=4)
    fresh = add_flag(db, intern, "R3", "medium", days=4)
    db.commit()
    items = {item["id"]: item for item in client.get("/api/signals").json()["items"]}
    assert (items[flag.id]["waiting_days"], items[flag.id]["overdue"]) == (4, True)
    assert (items[fresh.id]["waiting_days"], items[fresh.id]["overdue"]) == (4, False)
    taken = client.post(f"/api/signals/{flag.id}/actions", json={"state": "in_progress"}).json()
    assert (taken["waiting_days"], taken["overdue"]) == (None, False)


# --- focus ------------------------------------------------------------------


def test_focus_has_one_entry_per_intern(client, db):
    intern = add_intern(db, "Анна", Mentor(external_id="m-1", name="Мария"))
    low = add_flag(db, intern, "R1", "medium", days=1)
    top = add_flag(db, intern, "R6", "high", days=1)
    add_flag(db, intern, "R5", "low")
    db.commit()
    item, = get_focus(client)
    assert item == {
        "intern_id": intern.id, "name": "Анна", "mentor_name": "Мария", "severity": "high",
        "main_reason": plain_reason(top), "reasons": [plain_reason(top), plain_reason(low)],
        "signal_id": top.id, "state": "new", "waiting_days": 1, "overdue": False,
    }


def test_focus_skips_reviewed_low_inactive_and_other_months(client, db):
    done = add_intern(db, "Готово")
    add_flag(db, done, "R6", "high", days=9, state="reviewed")
    partly = add_intern(db, "Частично")
    add_flag(db, partly, "R6", "high", state="reviewed")
    left = add_flag(db, partly, "R1", "medium", state="in_progress")
    add_flag(db, add_intern(db, "Низкий"), "R1", "low", days=90)
    add_flag(db, add_intern(db, "Снят"), "R6", "high", status="inactive")
    add_flag(db, add_intern(db, "Март"), "R6", "high", period=date(2026, 3, 1))
    db.commit()
    item, = get_focus(client)
    assert (item["name"], item["signal_id"], item["severity"]) == ("Частично", left.id, "medium")
    assert (item["state"], item["waiting_days"], item["overdue"]) == ("in_progress", None, False)
    assert item["reasons"] == [plain_reason(left)]


def test_focus_order_and_limit(client, db):
    for name, severity, days, state in [
        ("Е-лишний", "medium", 0, None),
        ("Д-средний", "medium", 1, None),
        ("Г-высокий в работе", "high", 30, "in_progress"),
        ("В-высокий", "high", 2, None),
        ("Б-просрочен средний", "medium", 8, None),
        ("А2-просрочен", "high", 4, None),
        ("А1-просрочен", "high", 4, None),
    ]:
        add_flag(db, add_intern(db, name), "R6", severity, days=days, state=state)
    db.commit()
    focus = get_focus(client)
    assert [item["name"] for item in focus] == [
        "А1-просрочен", "А2-просрочен", "Б-просрочен средний", "В-высокий", "Г-высокий в работе",
    ]
    assert [item["overdue"] for item in focus] == [True, True, True, False, False]


def test_untaken_signal_is_preferred_among_equally_severe(client, db):
    intern = add_intern(db, "Анна")
    add_flag(db, intern, "R2", "high", state="in_progress")
    waiting = add_flag(db, intern, "R6", "high", days=5)
    db.commit()
    item, = get_focus(client)
    assert (item["signal_id"], item["overdue"]) == (waiting.id, True)


def test_summary_priority_and_main_reason(client, db):
    calm, flagged = add_intern(db, "Без сигналов"), add_intern(db, "С сигналами")
    add_flag(db, flagged, "R1", "medium")
    top = add_flag(db, flagged, "R6", "high", state="reviewed")
    db.commit()
    interns = client.get("/api/dashboard/summary?period=2026-01").json()["interns"]
    by_id = {item["id"]: item for item in interns}
    assert (by_id[calm.id]["priority"], by_id[calm.id]["main_reason"]) == (None, None)
    assert (by_id[flagged.id]["priority"], by_id[flagged.id]["main_reason"]) == ("high", plain_reason(top))


def test_card_agenda_uses_latest_month_with_open_flags(client, db):
    intern, calm = add_intern(db, "Анна"), add_intern(db, "Спокойный")
    add_flag(db, intern, "R7", "high")
    add_flag(db, intern, "R1", "medium", period=date(2026, 2, 1))
    add_flag(db, intern, "R6", "high", period=date(2026, 2, 1))
    add_flag(db, intern, "R3", "high", period=date(2026, 3, 1), status="inactive")
    db.commit()
    agenda = client.get(f"/api/interns/{intern.id}").json()["agenda"]
    assert [item["rule_code"] for item in agenda] == ["R6", "R1"]
    assert set(agenda[0]) == {"rule_code", "question", "why"}
    assert client.get(f"/api/interns/{calm.id}").json()["agenda"] == []


def test_summary_query_count_does_not_grow(client, db):
    def count_queries():
        queries = []

        def record(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert client.get("/api/dashboard/summary?period=2026-01").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(queries)

    mentor = Mentor(external_id="m-1", name="Мария")
    add_flag(db, add_intern(db, "Первый", mentor), "R6", "high", state="in_progress")
    db.commit()
    before = count_queries()
    for index in range(12):
        add_flag(db, add_intern(db, f"Стажёр {index}", mentor), "R6", "high", state="in_progress")
    db.commit()
    assert count_queries() == before


def test_intent_to_leave_outranks_other_high_signals():
    from app.services.agenda import rule_rank

    assert sorted(["R3", "R6", "R7", "R2"], key=rule_rank)[0] == "R7"


def test_default_month_is_the_newest_with_answers(client, db):
    from app.seed_data import seed_database

    seed_database(db, analyze=True)
    assert client.get("/api/dashboard/summary").json()["period"] == "2026-03-01"
    with_answers = [item["period"] for item in client.get("/api/periods").json() if item["has_answers"]]
    assert with_answers[0] == "2026-03-01"


def test_leaving_intern_opens_the_focus_list_and_card(client, db):
    from app.seed_data import seed_database

    seed_database(db, analyze=True)
    first = client.get("/api/dashboard/summary").json()["focus"][0]
    assert "В ответе есть тема ухода" in first["reasons"]
    card = client.get(f"/api/interns/{first['intern_id']}").json()
    periods = [flag["period"] for flag in card["flags"]]
    assert periods == sorted(periods, reverse=True)
    assert card["flags"][0]["rule_code"] == "R7"
