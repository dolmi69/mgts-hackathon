from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StringConstraints
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload, load_only, selectinload

from ..auth import can_dashboard_write, require_dashboard_auth, require_dashboard_write
from ..config import settings
from ..database import get_db
from ..models import DEMO_INTERN_STATUS, Answer, Intern, RiskFlag, SignalAction
from ..services.survey import latest_monthly_answers
from ..utils import format_period, parse_period

router = APIRouter(prefix="/api/signals", tags=["HR signals"])

WorkflowState = Literal["new", "in_progress", "reviewed"]
ActionState = Literal["in_progress", "reviewed"]
Severity = Literal["high", "medium", "low"]
Scope = Literal["program", "demo"]


class SignalUpdate(BaseModel):
    state: ActionState
    assigned_to: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class ActionOut(BaseModel):
    state: ActionState
    actor: str
    assigned_to: str
    note: str
    created_at: datetime


class NextSurvey(BaseModel):
    period_label: str
    outcome: Literal["waiting", "repeated", "not_repeated"]


class SignalOut(BaseModel):
    id: int
    intern_id: int
    intern_name: str
    direction: str | None
    period: date
    period_label: str
    rule_code: str
    severity: Severity
    title: str
    explanation: str
    evidence_answer_ids: list[int]
    active: bool
    state: WorkflowState
    assigned_to: str | None
    history: list[ActionOut]
    next_survey: NextSurvey | None


class InboxOut(BaseModel):
    items: list[SignalOut]
    can_write: bool
    demo_available: bool
    next_offset: int | None


def _next_surveys(db: Session, flags: list[RiskFlag]) -> dict[int, NextSurvey]:
    tracked = [flag for flag in flags if flag.actions]
    if not tracked:
        return {}

    intern_ids = {flag.intern_id for flag in tracked}
    answers = list(
        db.scalars(
            select(Answer)
            .where(Answer.intern_id.in_(intern_ids))
            .options(load_only(
                Answer.intern_id, Answer.period, Answer.respondent_type,
                Answer.analysis_status, Answer.submitted_at, Answer.created_at,
            ))
        )
    )
    surveys: dict[int, dict[date, dict[str, str]]] = defaultdict(dict)
    roles_by_id = {answer.id: answer.respondent_type for answer in answers}
    monthly = latest_monthly_answers(answers)
    for answer in sorted(monthly.values(), key=lambda item: item.period):
        surveys[answer.intern_id].setdefault(answer.period, {})[answer.respondent_type] = answer.analysis_status

    active_signals = set(
        db.execute(
            select(RiskFlag.intern_id, RiskFlag.period, RiskFlag.rule_code)
            .where(RiskFlag.intern_id.in_(intern_ids), RiskFlag.status == "open")
        ).tuples().all()
    )
    results = {}
    for flag in tracked:
        next_period = next((period for period in surveys[flag.intern_id] if period > flag.period), None)
        if next_period is None:
            continue
        required_roles = {
            roles_by_id[answer_id]
            for answer_id in flag.evidence_answer_ids
            if answer_id in roles_by_id
        } or {"intern"}
        statuses = surveys[flag.intern_id][next_period]
        outcome: Literal["waiting", "repeated", "not_repeated"]
        if (flag.intern_id, next_period, flag.rule_code) in active_signals:
            outcome = "repeated"
        elif any(statuses.get(role) != "done" for role in required_roles):
            outcome = "waiting"
        else:
            outcome = "not_repeated"
        results[flag.id] = NextSurvey(period_label=format_period(next_period), outcome=outcome)
    return results


def _signal_out(flag: RiskFlag, next_survey: NextSurvey | None) -> SignalOut:
    last = flag.actions[-1] if flag.actions else None
    return SignalOut(
        id=flag.id,
        intern_id=flag.intern_id,
        intern_name=flag.intern.name,
        direction=flag.intern.direction,
        period=flag.period,
        period_label=format_period(flag.period),
        rule_code=flag.rule_code,
        severity=flag.severity,
        title=flag.title,
        explanation=flag.explanation,
        evidence_answer_ids=flag.evidence_answer_ids,
        active=flag.status == "open",
        state=last.state if last else "new",
        assigned_to=last.assigned_to if last else None,
        history=[
            ActionOut(
                state=action.state,
                actor=action.actor,
                assigned_to=action.assigned_to,
                note=action.note,
                created_at=action.created_at.replace(
                    tzinfo=action.created_at.tzinfo or timezone.utc
                ).astimezone(timezone.utc),
            )
            for action in flag.actions
        ],
        next_survey=next_survey,
    )


@router.get("", response_model=InboxOut)
def list_signals(
    actor: Annotated[str | None, Depends(require_dashboard_auth)],
    db: Annotated[Session, Depends(get_db)],
    scope: Scope = "program",
    state: WorkflowState | None = None,
    severity: Severity | None = None,
    period: str | None = None,
    intern_id: Annotated[int | None, Query(gt=0)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> InboxOut:
    if scope == "demo" and not settings.demo_feedback_enabled:
        raise HTTPException(status_code=404, detail="Демо-сигналы отключены.")

    latest = (
        select(SignalAction.flag_id, func.max(SignalAction.id).label("last_id"))
        .group_by(SignalAction.flag_id)
        .subquery()
    )
    current_state = func.coalesce(SignalAction.state, "new")
    stmt = (
        select(RiskFlag)
        .join(Intern)
        .outerjoin(latest, latest.c.flag_id == RiskFlag.id)
        .outerjoin(SignalAction, SignalAction.id == latest.c.last_id)
        .where(
            Intern.status == DEMO_INTERN_STATUS if scope == "demo" else Intern.status != DEMO_INTERN_STATUS,
            or_(RiskFlag.status == "open", latest.c.last_id.is_not(None)),
        )
        .options(contains_eager(RiskFlag.intern), selectinload(RiskFlag.actions))
    )
    if state:
        stmt = stmt.where(current_state == state)
    if severity:
        stmt = stmt.where(RiskFlag.severity == severity)
    if period:
        try:
            stmt = stmt.where(RiskFlag.period == parse_period(period))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректный месяц.") from exc
    if intern_id is not None:
        stmt = stmt.where(RiskFlag.intern_id == intern_id)

    flags = list(
        db.scalars(
            stmt.order_by(
                case((current_state == "reviewed", 1), else_=0),
                RiskFlag.period.desc(),
                case((RiskFlag.severity == "high", 0), (RiskFlag.severity == "medium", 1), else_=2),
                RiskFlag.id.desc(),
            ).offset(offset).limit(limit + 1)
        )
    )
    page = flags[:limit]
    followups = _next_surveys(db, page)
    return InboxOut(
        items=[_signal_out(flag, followups.get(flag.id)) for flag in page],
        can_write=can_dashboard_write(actor),
        demo_available=settings.demo_feedback_enabled,
        next_offset=offset + limit if len(flags) > limit else None,
    )


@router.post("/{flag_id}/actions", response_model=SignalOut)
def update_signal(
    flag_id: int,
    update: SignalUpdate,
    actor: Annotated[str, Depends(require_dashboard_write)],
    db: Annotated[Session, Depends(get_db)],
) -> SignalOut:
    stmt = (
        select(RiskFlag)
        .where(RiskFlag.id == flag_id)
        .options(joinedload(RiskFlag.intern, innerjoin=True), selectinload(RiskFlag.actions))
        .with_for_update(of=RiskFlag)
    )
    flag = db.scalar(stmt)
    if flag is None or (flag.intern.status == DEMO_INTERN_STATUS and not settings.demo_feedback_enabled):
        raise HTTPException(status_code=404, detail="Сигнал не найден.")

    accepted = next((item for item in flag.actions if item.state == update.state), None)
    taken = next((item for item in flag.actions if item.state == "in_progress"), None)
    if update.state == "reviewed":
        if taken is None:
            raise HTTPException(status_code=409, detail="Сначала возьмите сигнал в работу.")
        if update.assigned_to and update.assigned_to != taken.assigned_to:
            raise HTTPException(status_code=409, detail="Ответственный назначается при взятии в работу.")
        assignee = taken.assigned_to
    else:
        assignee = update.assigned_to or actor[:80]

    if accepted is not None:
        if accepted.assigned_to != assignee or accepted.note != update.note or accepted.actor != actor:
            raise HTTPException(status_code=409, detail="Это действие уже записано.")
    else:
        if update.state == "in_progress" and flag.status != "open":
            raise HTTPException(status_code=409, detail="Причина сигнала больше не срабатывает.")
        flag.actions.append(SignalAction(
            state=update.state, actor=actor, assigned_to=assignee, note=update.note,
        ))
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Сигнал уже изменён. Обновите список.") from exc

    return _signal_out(flag, _next_surveys(db, [flag]).get(flag.id))
