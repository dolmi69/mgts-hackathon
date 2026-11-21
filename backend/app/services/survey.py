from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

from ..models import Answer
from ..utils import add_months


def survey_deadline(period: date, *, grace_days: int) -> date:
    return add_months(period, 1) + timedelta(days=max(grace_days, 0) - 1)


def last_closed_period(today: date, *, grace_days: int) -> date:
    cutoff = today - timedelta(days=max(grace_days, 0) + 1)
    cutoff_period = cutoff.replace(day=1)
    if cutoff == survey_deadline(cutoff_period, grace_days=0):
        return cutoff_period
    return add_months(cutoff_period, -1)


def expected_periods(start: date | None, program_months: int) -> list[date]:
    if start is None:
        return []
    return [add_months(start, offset) for offset in range(program_months)]


def submission_order(answer: Answer) -> tuple[datetime, int]:
    submitted = answer.submitted_at or answer.created_at or datetime.min
    timestamp = submitted.replace(tzinfo=timezone.utc) if submitted.tzinfo is None else submitted
    return timestamp.astimezone(timezone.utc), answer.id


def latest_monthly_answers(answers: Iterable[Answer]) -> dict[tuple[int, date, str], Answer]:
    latest: dict[tuple[int, date, str], Answer] = {}
    for answer in answers:
        key = (answer.intern_id, answer.period, answer.respondent_type)
        if key not in latest or submission_order(answer) > submission_order(latest[key]):
            latest[key] = answer
    return latest
