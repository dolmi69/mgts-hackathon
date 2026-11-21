from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import can_dashboard_write, require_dashboard_auth
from ..config import settings
from ..database import get_db
from ..models import DEMO_INTERN_STATUS, Answer, Intern, RiskFlag
from ..schemas import (
    DashboardSummary,
    CalendarCounts,
    CalendarDirection,
    DynamicsPoint,
    InternCard,
    InternStats,
    InternSummary,
    KpiOut,
    PeriodOut,
    SeriesPoint,
    SurveyCalendar,
    TopicCount,
    TopicMap,
    TopicMapCell,
    TopicMapDirection,
    build_answer_out,
    build_flag_out,
)
from ..services.rules import load_rules_config
from ..services.survey import (
    expected_periods,
    latest_monthly_answers,
    survey_deadline,
)
from ..utils import add_months, format_period, parse_period, severity_rank

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_dashboard_auth)])


def _program_interns(db: Session) -> list[Intern]:
    return list(
        db.scalars(
            select(Intern)
            .where(Intern.status != DEMO_INTERN_STATUS)
            .options(selectinload(Intern.mentor))
        ).all()
    )


def _observed_periods(db: Session) -> dict[int, set[date]]:
    observed: dict[int, set[date]] = defaultdict(set)
    rows = db.execute(
        select(Answer.intern_id, Answer.period)
        .join(Intern)
        .where(Intern.status != DEMO_INTERN_STATUS)
        .distinct()
    )
    for intern_id, period in rows:
        observed[intern_id].add(period)
    return observed


def _enrollment_periods(
    interns: list[Intern], observed: dict[int, set[date]], program_months: int
) -> dict[int, set[date]]:
    enrollment = {}
    for intern in interns:
        received = observed.get(intern.id, set())
        scheduled = expected_periods(intern.start_period, program_months)
        if intern.start_period is None and received:
            scheduled = [
                period for period in expected_periods(min(received), program_months)
                if period <= max(received)
            ]
        enrollment[intern.id] = received.union(scheduled)
    return enrollment


def _periods(db: Session) -> list[date]:
    observed = _observed_periods(db)
    enrollment = _enrollment_periods(
        _program_interns(db), observed, int(load_rules_config()["program_months"])
    )
    current = date.today().replace(day=1)
    available = set().union(*observed.values())
    available.update(period for periods in enrollment.values() for period in periods if period <= current)
    return sorted(available, reverse=True)


def _selected_period(db: Session, period: str | None) -> date:
    if period:
        try:
            return parse_period(period)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    available = _periods(db)
    return available[0] if available else date.today().replace(day=1)


@router.get("/api/periods", response_model=list[PeriodOut])
def list_periods(db: Session = Depends(get_db)) -> list[PeriodOut]:
    return [PeriodOut(period=period, label=format_period(period)) for period in _periods(db)]


@router.get("/api/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    period: str | None = Query(default=None, description="YYYY-MM"),
    db: Session = Depends(get_db),
) -> DashboardSummary:
    selected = _selected_period(db, period)
    answers = list(
        db.scalars(
            select(Answer)
            .join(Intern)
            .where(Intern.status != DEMO_INTERN_STATUS)
            .options(selectinload(Answer.analysis))
            .order_by(Answer.period, Answer.id)
        ).all()
    )
    interns = _program_interns(db)
    flags = list(
        db.scalars(
            select(RiskFlag)
            .join(Intern)
            .where(
                RiskFlag.period == selected,
                RiskFlag.status == "open",
                Intern.status != DEMO_INTERN_STATUS,
            )
        ).all()
    )
    by_slot = latest_monthly_answers(answers)
    by_period: dict[date, list[Answer]] = defaultdict(list)
    observed: dict[int, set[date]] = defaultdict(set)
    for answer in by_slot.values():
        by_period[answer.period].append(answer)
        observed[answer.intern_id].add(answer.period)
    enrollment = _enrollment_periods(interns, observed, int(load_rules_config()["program_months"]))
    eligible = [intern for intern in interns if selected in enrollment[intern.id]]
    as_of = max(by_period, default=selected)
    period_answers = by_period.get(selected, [])
    flags_by_intern: dict[int, list[RiskFlag]] = {}
    for flag in flags:
        flags_by_intern.setdefault(flag.intern_id, []).append(flag)

    intern_answers = [
        answer for answer in period_answers if answer.respondent_type == "intern"
    ]
    mentor_answers = [
        answer for answer in period_answers if answer.respondent_type == "mentor"
    ]
    responded_ids = {answer.intern_id for answer in intern_answers}
    mentor_responded_ids = {answer.intern_id for answer in mentor_answers}
    avg_score = mean([a.overall_score for a in intern_answers]) if intern_answers else None

    previous_period = add_months(selected, -1)

    summaries: list[InternSummary] = []
    for intern in eligible:
        current = by_slot.get((intern.id, selected, "intern"))
        mentor_current = by_slot.get((intern.id, selected, "mentor"))
        previous = by_slot.get((intern.id, previous_period, "intern"))
        current_score = current.overall_score if current is not None else None
        trend = (
            current_score - previous.overall_score
            if current_score is not None and previous is not None
            else None
        )
        statuses = {
            answer.analysis_status for answer in (current, mentor_current) if answer is not None
        }
        if statuses & {"pending", "processing"}:
            analysis_status = "pending"
        elif "failed" in statuses:
            analysis_status = "failed"
        elif "done" in statuses:
            analysis_status = "done"
        else:
            analysis_status = "none"
        intern_flags = sorted(
            flags_by_intern.get(intern.id, []),
            key=lambda flag: (severity_rank(flag.severity), flag.rule_code),
        )
        summaries.append(
            InternSummary(
                id=intern.id,
                external_id=intern.external_id,
                name=intern.name,
                direction=intern.direction,
                mentor_name=intern.mentor.name if intern.mentor else None,
                avg_score=current_score,
                trend=trend,
                flags=[build_flag_out(flag) for flag in intern_flags],
                analysis_status=analysis_status,
                last_period=max(observed[intern.id], default=None),
            )
        )

    summaries.sort(
        key=lambda item: (
            min((severity_rank(flag.severity) for flag in item.flags), default=3),
            item.avg_score if item.avg_score is not None else 99,
            item.name,
        )
    )

    dynamics: list[DynamicsPoint] = []
    current_month = date.today().replace(day=1)
    periods = set(by_period)
    periods.update(period for dates in enrollment.values() for period in dates if period <= current_month)
    for value in sorted(periods):
        group = by_period.get(value, [])
        intern_group = [answer.overall_score for answer in group if answer.respondent_type == "intern"]
        mentor_group = [answer.overall_score for answer in group if answer.respondent_type == "mentor"]
        dynamics.append(
            DynamicsPoint(
                period=value,
                period_label=format_period(value),
                intern_avg=mean(intern_group) if intern_group else None,
                mentor_avg=mean(mentor_group) if mentor_group else None,
                responses=len(group),
            )
        )

    topics_by_intern: dict[int, set[str]] = defaultdict(set)
    for answer in period_answers:
        if answer.analysis is not None and answer.analysis.status == "done":
            topics_by_intern[answer.intern_id].update(answer.analysis.problem_topics or [])
    topic_counter = Counter(topic for topics in topics_by_intern.values() for topic in topics)
    top_topics = [
        TopicCount(topic=topic, count=count)
        for topic, count in sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]

    return DashboardSummary(
        period=selected,
        period_label=format_period(selected),
        as_of=as_of,
        demo_feedback_enabled=settings.demo_feedback_enabled,
        kpis=KpiOut(
            interns=len(eligible),
            responded=len(responded_ids),
            response_rate=(len(responded_ids) / len(eligible)) if eligible else 0.0,
            mentor_responded=len(mentor_responded_ids),
            mentor_response_rate=(len(mentor_responded_ids) / len(eligible)) if eligible else 0.0,
            avg_score=avg_score,
            flags_high=sum(1 for flag in flags if flag.severity == "high"),
            flags_medium=sum(1 for flag in flags if flag.severity == "medium"),
            flags_low=sum(1 for flag in flags if flag.severity == "low"),
        ),
        dynamics=dynamics,
        top_topics=top_topics,
        interns=summaries,
    )


def _direction(intern: Intern) -> str:
    return (intern.direction or "").strip() or "Без направления"


@router.get("/api/dashboard/calendar", response_model=SurveyCalendar)
def survey_calendar(
    period: str | None = Query(default=None, description="YYYY-MM"),
    db: Session = Depends(get_db),
) -> SurveyCalendar:
    selected = _selected_period(db, period)
    config = load_rules_config()
    today = date.today()
    deadline = survey_deadline(selected, grace_days=int(config["r4"]["grace_days"]))
    phase = "upcoming" if selected > today else "closed" if today > deadline else "open"
    interns = _program_interns(db)
    enrollment = _enrollment_periods(interns, _observed_periods(db), int(config["program_months"]))
    received = set(
        db.execute(select(Answer.intern_id, Answer.respondent_type).where(Answer.period == selected))
    )
    groups: dict[str, set[int]] = defaultdict(set)
    for intern in interns:
        if selected in enrollment[intern.id]:
            groups[_direction(intern)].add(intern.id)

    def counts(intern_ids: set[int]) -> CalendarCounts:
        expected = len(intern_ids)
        interns_received = sum((intern_id, "intern") in received for intern_id in intern_ids)
        mentors_received = sum((intern_id, "mentor") in received for intern_id in intern_ids)
        return CalendarCounts(
            expected=expected,
            intern_responded=interns_received,
            mentor_responded=mentors_received,
            intern_missing=expected - interns_received,
            mentor_missing=expected - mentors_received,
        )

    totals = counts(set().union(*groups.values()))
    label = format_period(selected)
    reminder = None
    if phase != "upcoming" and (totals.intern_missing or totals.mentor_missing):
        reminder = (
            f"Привет! Напоминаем об анкете по стажировке за {label}. "
            f"Срок сдачи — {deadline:%d.%m.%Y}. "
            "Если вы ещё не ответили, заполните анкету по ссылке от координатора. Спасибо!"
        )
    return SurveyCalendar(
        period=selected,
        period_label=label,
        deadline=deadline,
        phase=phase,
        totals=totals,
        directions=[
            CalendarDirection(direction=direction, **counts(groups[direction]).model_dump())
            for direction in sorted(groups, key=str.casefold)
        ],
        reminder_draft=reminder,
    )


MIN_TOPIC_GROUP_SIZE = 3


@router.get("/api/dashboard/topics", response_model=TopicMap)
def survey_topics(
    period: str | None = Query(default=None, description="YYYY-MM"),
    months: int = Query(default=6, ge=1, le=12),
    db: Session = Depends(get_db),
) -> TopicMap:
    selected = _selected_period(db, period)
    periods = [add_months(selected, offset) for offset in range(1 - months, 1)]
    interns = _program_interns(db)
    groups: dict[str, set[int]] = defaultdict(set)
    for intern in interns:
        groups[_direction(intern)].add(intern.id)
    program_ids = {intern.id for intern in interns}
    answers = db.scalars(
        select(Answer)
        .join(Intern)
        .where(
            Answer.period.in_(periods),
            Answer.respondent_type == "intern",
            Intern.status != DEMO_INTERN_STATUS,
        )
        .options(selectinload(Answer.analysis))
    ).all()
    analyzed: dict[date, dict[int, set[str]]] = defaultdict(dict)
    for answer in latest_monthly_answers(answers).values():
        if answer.analysis is not None and answer.analysis.status == "done":
            analyzed[answer.period][answer.intern_id] = set(answer.analysis.problem_topics or [])

    def cell(target: date, group: set[int]) -> TopicMapCell:
        received = analyzed[target]
        members = group.intersection(received)
        if len(members) < MIN_TOPIC_GROUP_SIZE:
            return TopicMapCell(period=target, responded=None, suppressed=True, topics=[])
        topics = Counter(topic for member in members for topic in received[member])
        return TopicMapCell(
            period=target,
            responded=len(members),
            suppressed=False,
            topics=[
                TopicCount(topic=topic, count=count)
                for topic, count in sorted(topics.items(), key=lambda item: (-item[1], item[0]))
            ],
        )

    rows = [TopicMapDirection(direction="Вся программа", periods=[cell(value, program_ids) for value in periods])]
    rows.extend(
        TopicMapDirection(direction=direction, periods=[cell(value, groups[direction]) for value in periods])
        for direction in sorted(groups, key=str.casefold)
    )
    return TopicMap(
        period=selected,
        months=months,
        min_group_size=MIN_TOPIC_GROUP_SIZE,
        periods=[PeriodOut(period=value, label=format_period(value)) for value in periods],
        directions=rows,
    )


@router.get("/api/interns/{intern_id}", response_model=InternCard)
def intern_card(
    intern_id: int,
    db: Session = Depends(get_db),
    actor: str | None = Depends(require_dashboard_auth),
) -> InternCard:
    intern = db.get(Intern, intern_id)
    if intern is None:
        raise HTTPException(status_code=404, detail="Стажёр не найден")

    answers = list(
        db.scalars(
            select(Answer)
            .where(Answer.intern_id == intern_id)
            .options(selectinload(Answer.analysis))
            .order_by(Answer.period, Answer.id)
        ).all()
    )
    flags = list(db.scalars(
        select(RiskFlag).where(RiskFlag.intern_id == intern_id, RiskFlag.status == "open")
    ).all())
    flags.sort(
        key=lambda flag: (flag.period, -severity_rank(flag.severity)), reverse=True
    )

    by_slot = latest_monthly_answers(answers)
    periods = sorted({answer.period for answer in answers})
    series: list[SeriesPoint] = []
    for period in periods:
        intern_answer = by_slot.get((intern_id, period, "intern"))
        mentor_answer = by_slot.get((intern_id, period, "mentor"))
        series.append(
            SeriesPoint(
                period=period,
                period_label=format_period(period),
                intern_score=intern_answer.overall_score if intern_answer else None,
                mentor_score=mentor_answer.overall_score if mentor_answer else None,
            )
        )

    intern_scores = [
        answer.overall_score for answer in by_slot.values() if answer.respondent_type == "intern"
    ]
    mentor_scores = [
        answer.overall_score for answer in by_slot.values() if answer.respondent_type == "mentor"
    ]
    stats = InternStats(
        total_answers=len(answers),
        analyzed=sum(1 for answer in answers if answer.analysis_status == "done"),
        pending=sum(
            1 for answer in answers if answer.analysis_status in {"pending", "processing"}
        ),
        failed=sum(1 for answer in answers if answer.analysis_status == "failed"),
        avg_intern=mean(intern_scores) if intern_scores else None,
        avg_mentor=mean(mentor_scores) if mentor_scores else None,
    )

    return InternCard(
        id=intern.id,
        external_id=intern.external_id,
        name=intern.name,
        direction=intern.direction,
        mentor_name=intern.mentor.name if intern.mentor else None,
        start_period=intern.start_period,
        is_demo=intern.status == DEMO_INTERN_STATUS,
        can_write=can_dashboard_write(actor),
        stats=stats,
        series=series,
        answers=[
            build_answer_out(
                answer,
                current_for_month=by_slot[(intern_id, answer.period, answer.respondent_type)].id == answer.id,
            )
            for answer in answers
        ],
        flags=[build_flag_out(flag) for flag in flags],
    )
