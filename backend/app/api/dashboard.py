from __future__ import annotations

from collections import Counter
from datetime import date
from statistics import mean

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import require_dashboard_auth
from ..database import get_db
from ..models import Answer, Intern, RiskFlag
from ..schemas import (
    DashboardSummary,
    DynamicsPoint,
    InternCard,
    InternStats,
    InternSummary,
    KpiOut,
    PeriodOut,
    SeriesPoint,
    TopicCount,
    build_answer_out,
    build_flag_out,
)
from ..utils import format_period, parse_period, severity_rank

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_dashboard_auth)])


def _periods(db: Session) -> list[date]:
    return list(
        db.scalars(select(Answer.period).distinct().order_by(Answer.period.desc())).all()
    )


def _selected_period(db: Session, period: str | None) -> date:
    if period:
        try:
            return parse_period(period)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    latest = db.scalar(select(Answer.period).order_by(Answer.period.desc()).limit(1))
    return latest or date.today().replace(day=1)


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
            .options(selectinload(Answer.analysis))
            .order_by(Answer.period, Answer.id)
        ).all()
    )
    interns = list(db.scalars(select(Intern).options(selectinload(Intern.mentor))).all())
    flags = list(db.scalars(select(RiskFlag).where(RiskFlag.period == selected)).all())
    all_periods = sorted({answer.period for answer in answers})
    as_of = max(all_periods) if all_periods else selected

    period_answers = [answer for answer in answers if answer.period == selected]
    flags_by_intern: dict[int, list[RiskFlag]] = {}
    for flag in flags:
        flags_by_intern.setdefault(flag.intern_id, []).append(flag)

    intern_answers = [
        answer for answer in period_answers if answer.respondent_type == "intern"
    ]
    mentor_answers = [
        answer for answer in period_answers if answer.respondent_type == "mentor"
    ]
    responded_ids = {answer.intern_id for answer in period_answers}
    avg_score = mean([a.overall_score for a in intern_answers]) if intern_answers else None

    previous_periods = [value for value in all_periods if value < selected]
    previous_period = previous_periods[-1] if previous_periods else None

    summaries: list[InternSummary] = []
    for intern in interns:
        current = next(
            (
                answer
                for answer in period_answers
                if answer.intern_id == intern.id and answer.respondent_type == "intern"
            ),
            None,
        )
        mentor_current = next(
            (
                answer
                for answer in period_answers
                if answer.intern_id == intern.id and answer.respondent_type == "mentor"
            ),
            None,
        )
        previous = None
        if previous_period is not None:
            previous = next(
                (
                    answer
                    for answer in answers
                    if answer.intern_id == intern.id
                    and answer.respondent_type == "intern"
                    and answer.period == previous_period
                ),
                None,
            )
        current_score = current.overall_score if current is not None else None
        trend = (
            current_score - previous.overall_score
            if current_score is not None and previous is not None
            else None
        )
        intern_period_answers = [
            answer for answer in period_answers if answer.intern_id == intern.id
        ]
        statuses = {answer.analysis_status for answer in intern_period_answers}
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
        last_answer = next(
            (answer for answer in reversed(answers) if answer.intern_id == intern.id),
            None,
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
                last_period=last_answer.period if last_answer else None,
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
    for value in all_periods:
        group = [answer for answer in answers if answer.period == value]
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

    topic_counter: Counter[str] = Counter()
    for answer in period_answers:
        if answer.analysis is not None and answer.analysis.status == "done":
            for topic in answer.analysis.problem_topics or []:
                topic_counter[topic] += 1
    top_topics = [
        TopicCount(topic=topic, count=count)
        for topic, count in topic_counter.most_common(8)
    ]

    return DashboardSummary(
        period=selected,
        period_label=format_period(selected),
        as_of=as_of,
        kpis=KpiOut(
            interns=len(interns),
            responded=len(responded_ids),
            response_rate=(len(responded_ids) / len(interns)) if interns else 0.0,
            avg_score=avg_score,
            flags_high=sum(1 for flag in flags if flag.severity == "high"),
            flags_medium=sum(1 for flag in flags if flag.severity == "medium"),
            flags_low=sum(1 for flag in flags if flag.severity == "low"),
        ),
        dynamics=dynamics,
        top_topics=top_topics,
        interns=summaries,
    )


@router.get("/api/interns/{intern_id}", response_model=InternCard)
def intern_card(intern_id: int, db: Session = Depends(get_db)) -> InternCard:
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
    flags = list(
        db.scalars(select(RiskFlag).where(RiskFlag.intern_id == intern_id)).all()
    )
    flags.sort(
        key=lambda flag: (flag.period, -severity_rank(flag.severity)), reverse=True
    )

    periods = sorted({answer.period for answer in answers})
    series: list[SeriesPoint] = []
    for period in periods:
        intern_answer = next(
            (
                answer
                for answer in answers
                if answer.period == period and answer.respondent_type == "intern"
            ),
            None,
        )
        mentor_answer = next(
            (
                answer
                for answer in answers
                if answer.period == period and answer.respondent_type == "mentor"
            ),
            None,
        )
        series.append(
            SeriesPoint(
                period=period,
                period_label=format_period(period),
                intern_score=intern_answer.overall_score if intern_answer else None,
                mentor_score=mentor_answer.overall_score if mentor_answer else None,
            )
        )

    intern_scores = [
        answer.overall_score for answer in answers if answer.respondent_type == "intern"
    ]
    mentor_scores = [
        answer.overall_score for answer in answers if answer.respondent_type == "mentor"
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
        stats=stats,
        series=series,
        answers=[build_answer_out(answer) for answer in answers],
        flags=[build_flag_out(flag) for flag in flags],
    )
