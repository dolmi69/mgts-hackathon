from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .utils import format_period, parse_period

RespondentType = Literal["intern", "mentor"]
Sentiment = Literal["positive", "neutral", "mixed", "negative"]
Severity = Literal["high", "medium", "low"]


class Scores(BaseModel):
    overall: float = Field(ge=1, le=5)
    workload: float | None = Field(default=None, ge=1, le=5)
    mentorship: float | None = Field(default=None, ge=1, le=5)


class CanonicalAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = "generic"
    external_answer_id: str = Field(min_length=1, max_length=128)
    form_id: str | None = None
    period: date
    respondent_type: RespondentType
    intern_external_id: str = Field(min_length=1, max_length=64)
    mentor_external_id: str | None = None
    respondent_external_id: str | None = None
    scores: Scores
    comment: str = ""
    submitted_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("period", mode="before")
    @classmethod
    def _parse_period(cls, value: object) -> date:
        return parse_period(value)

    @field_validator("comment", mode="before")
    @classmethod
    def _clean_comment(cls, value: object) -> str:
        return " ".join(str(value or "").split())

    @field_validator("submitted_at", mode="before")
    @classmethod
    def _parse_submitted_at(cls, value: object) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        raw = str(value).strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
        raise ValueError(f"unsupported datetime format: {value!r}")

    @model_validator(mode="after")
    def _fill_respondent(self) -> "CanonicalAnswer":
        if not self.respondent_external_id:
            self.respondent_external_id = (
                self.intern_external_id
                if self.respondent_type == "intern"
                else self.mentor_external_id
            )
        return self


class AIResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sentiment: Sentiment = "neutral"
    topics: list[str] = Field(default_factory=list)
    problem_topics: list[str] = Field(default_factory=list)
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("topics", "problem_topics", "evidence", mode="before")
    @classmethod
    def _list_of_strings(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        result: list[str] = []
        for item in value:  # type: ignore[union-attr]
            text = " ".join(str(item).split()).strip()
            if text and text not in result:
                result.append(text)
        return result

    @model_validator(mode="after")
    def _problem_topics_are_known(self) -> "AIResult":
        self.problem_topics = [t for t in self.problem_topics if t in self.topics]
        return self


class AnswerAnalysisOut(BaseModel):
    status: str
    sentiment: Sentiment | None = None
    topics: list[str] = Field(default_factory=list)
    problem_topics: list[str] = Field(default_factory=list)
    summary: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = None
    model: str | None = None
    prompt_version: str | None = None
    error: str | None = None


class AnswerOut(BaseModel):
    id: int
    period: date
    period_label: str
    respondent_type: RespondentType
    scores: dict[str, Any]
    overall_score: float
    comment: str
    submitted_at: datetime | None = None
    current_for_month: bool = True
    analysis_status: str
    analysis: AnswerAnalysisOut | None = None


class FlagOut(BaseModel):
    id: int
    intern_id: int
    period: date
    period_label: str
    rule_code: str
    severity: Severity
    title: str
    explanation: str
    evidence_answer_ids: list[int] = Field(default_factory=list)
    status: str


class SeriesPoint(BaseModel):
    period: date
    period_label: str
    intern_score: float | None = None
    mentor_score: float | None = None


class DynamicsPoint(BaseModel):
    period: date
    period_label: str
    intern_avg: float | None = None
    mentor_avg: float | None = None
    responses: int = 0


class TopicCount(BaseModel):
    topic: str
    count: int


class KpiOut(BaseModel):
    interns: int
    responded: int
    response_rate: float
    mentor_responded: int = 0
    mentor_response_rate: float = 0.0
    avg_score: float | None = None
    flags_high: int = 0
    flags_medium: int = 0
    flags_low: int = 0


class InternSummary(BaseModel):
    id: int
    external_id: str
    name: str
    direction: str | None = None
    mentor_name: str | None = None
    avg_score: float | None = None
    trend: float | None = None
    flags: list[FlagOut] = Field(default_factory=list)
    analysis_status: str
    last_period: date | None = None


class DashboardSummary(BaseModel):
    period: date
    period_label: str
    as_of: date
    demo_feedback_enabled: bool = False
    kpis: KpiOut
    dynamics: list[DynamicsPoint] = Field(default_factory=list)
    top_topics: list[TopicCount] = Field(default_factory=list)
    interns: list[InternSummary] = Field(default_factory=list)


class InternStats(BaseModel):
    total_answers: int
    analyzed: int
    pending: int
    failed: int
    avg_intern: float | None = None
    avg_mentor: float | None = None


class InternCard(BaseModel):
    id: int
    external_id: str
    name: str
    direction: str | None = None
    mentor_name: str | None = None
    start_period: date | None = None
    is_demo: bool = False
    can_write: bool = False
    stats: InternStats
    series: list[SeriesPoint] = Field(default_factory=list)
    answers: list[AnswerOut] = Field(default_factory=list)
    flags: list[FlagOut] = Field(default_factory=list)


class PeriodOut(BaseModel):
    period: date
    label: str


class CalendarCounts(BaseModel):
    expected: int = 0
    intern_responded: int = 0
    mentor_responded: int = 0
    intern_missing: int = 0
    mentor_missing: int = 0


class CalendarDirection(CalendarCounts):
    direction: str


class SurveyCalendar(BaseModel):
    period: date
    period_label: str
    deadline: date
    phase: Literal["upcoming", "open", "closed"]
    totals: CalendarCounts
    directions: list[CalendarDirection]
    reminder_draft: str | None = None


class TopicMapCell(BaseModel):
    period: date
    responded: int | None
    suppressed: bool
    topics: list[TopicCount]


class TopicMapDirection(BaseModel):
    direction: str
    periods: list[TopicMapCell]


class TopicMap(BaseModel):
    period: date
    months: int
    min_group_size: int
    periods: list[PeriodOut]
    directions: list[TopicMapDirection]


class WebhookResult(BaseModel):
    status: str
    created: bool
    answer_id: int | None = None
    message: str | None = None


class ImportResult(BaseModel):
    imported: int = 0
    duplicates: int = 0
    errors: list[str] = Field(default_factory=list)
    interns_created: int = 0
    interns_updated: int = 0
    pending_analysis: int = 0


class ReanalyzeResult(BaseModel):
    queued: int
    flags: int


class HealthOut(BaseModel):
    status: str
    db: str
    llm_provider: str
    app: str


def build_answer_out(answer: Any, *, current_for_month: bool = True) -> AnswerOut:
    analysis = None
    if answer.analysis is not None:
        a = answer.analysis
        analysis = AnswerAnalysisOut(
            status=a.status,
            sentiment=a.sentiment,
            topics=list(a.topics or []),
            problem_topics=list(a.problem_topics or []),
            summary=a.summary,
            evidence=list(a.evidence or []),
            confidence=a.confidence,
            model=a.model,
            prompt_version=a.prompt_version,
            error="Не удалось разобрать комментарий." if a.error else None,
        )
    return AnswerOut(
        id=answer.id,
        period=answer.period,
        period_label=format_period(answer.period),
        respondent_type=answer.respondent_type,
        scores=dict(answer.scores or {}),
        overall_score=answer.overall_score,
        comment=answer.comment or "",
        submitted_at=answer.submitted_at,
        current_for_month=current_for_month,
        analysis_status=answer.analysis_status,
        analysis=analysis,
    )


def build_flag_out(flag: Any) -> FlagOut:
    return FlagOut(
        id=flag.id,
        intern_id=flag.intern_id,
        period=flag.period,
        period_label=format_period(flag.period),
        rule_code=flag.rule_code,
        severity=flag.severity,
        title=flag.title,
        explanation=flag.explanation,
        evidence_answer_ids=list(flag.evidence_answer_ids or []),
        status=flag.status,
    )
