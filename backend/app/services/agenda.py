"""Conversation prompts and waiting time for HR signals. Pure functions, no LLM.

The wording helps HR open a conversation. It describes what was in the answers
and never judges the intern or predicts what they will do.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Literal, NamedTuple

from ..models import RiskFlag, utcnow
from ..schemas import AgendaItem
from ..utils import severity_rank
from .rules import TOPIC_LABELS

MAX_AGENDA_ITEMS = 5

SignalState = Literal["new", "in_progress", "reviewed"]

_PLAIN_REASONS = {
    "R1": "Оценка высокая, но в комментарии есть замечание",
    "R2": "Стажёр и наставник видят месяц по-разному",
    "R3": "Оценки снижаются несколько месяцев",
    "R4": "Не ответил на опрос",
    "R5": "Одна и та же трудность повторяется",
    "R6": "Низкая оценка месяца",
    "R7": "В ответе есть тема ухода",
}
_UNKNOWN_REASON = "Есть сигнал по ответам опроса"
_UNKNOWN_QUESTION = "Как проходит стажировка: что получается, а что хотелось бы изменить?"

# Used for R7 and for a repeating «leave» topic. The intern may not know who
# mentioned it (the mentor can be the source), so the question names no source
# and leaves the intern free to bring the subject up or not.
_CAREFUL_QUESTION = (
    "Как вам сейчас на стажировке: что хочется сохранить, а что изменить, "
    "чтобы было комфортнее?"
)
_CAREFUL_WHY = "В ответах затронута тема продолжения стажировки"

_TOPIC_QUESTIONS = {
    "workload": "Как сейчас складывается нагрузка и что помогло бы сделать её комфортной?",
    "tasks": "Какие задачи сейчас интересны и понятны, а какие хотелось бы изменить или уточнить?",
    "mentorship": "Как устроена работа с наставником: чего хватает, а чего хотелось бы больше?",
    "team": "Как складывается общение в команде и что сделало бы его удобнее?",
    "process": "Какие рабочие процессы сейчас отнимают больше всего сил и что можно упростить?",
    "growth": "Чему хочется научиться в ближайшие месяцы и какая поддержка для этого нужна?",
    "leave": _CAREFUL_QUESTION,
}
_REPEATED_QUESTION = (
    "Какие трудности возвращались в последние месяцы и что помогло бы их решить?"
)
_TOPIC_BY_LABEL = {label: topic for topic, label in TOPIC_LABELS.items()}
# Matches only the sentences written by rule R5 itself: at the start of the
# explanation or right after the closing quote of the previous sentence.
_REPEATED_TOPIC = re.compile(
    r"(?:^|(?<=»\. ))Тема «([^»]+)» повторяется в \d+ из \d+ последних комментариев стажёра\."
)


class SignalWait(NamedTuple):
    waiting_days: int | None
    overdue: bool


def signal_state(flag: RiskFlag) -> SignalState:
    return flag.actions[-1].state if flag.actions else "new"


def signal_wait(flag: RiskFlag, sla: dict[str, Any], *, now: datetime | None = None) -> SignalWait:
    """How long an open signal has been waiting for HR to take it.

    The clock starts when the signal was created in the system
    (``flag.created_at``), not when the survey was answered or the month ended:
    importing old answers today starts the clock today. Full 24-hour days are
    counted, so a signal becomes overdue at exactly ``high_days``/``medium_days``
    days. Taken, reviewed and inactive signals do not wait; "low" has no SLA.
    """
    if flag.status != "open" or signal_state(flag) != "new":
        return SignalWait(None, False)
    created = flag.created_at
    if created.tzinfo is None:  # SQLite returns naive UTC values
        created = created.replace(tzinfo=timezone.utc)
    days = max(((now or utcnow()) - created).days, 0)
    limit = sla.get(f"{flag.severity}_days") if flag.severity in {"high", "medium"} else None
    return SignalWait(days, limit is not None and days >= limit)


def rule_rank(rule_code: str) -> tuple[int, str]:
    """Order within one severity: the person's own words about leaving come first."""
    return (0 if rule_code == "R7" else 1, rule_code)


def plain_reason(flag: RiskFlag) -> str:
    """One plain phrase per rule, without rule codes."""
    return _PLAIN_REASONS.get(flag.rule_code, _UNKNOWN_REASON)


def _repeated_topics(explanation: str) -> list[str]:
    topics = [_TOPIC_BY_LABEL.get(label) for label in _REPEATED_TOPIC.findall(explanation)]
    return list(dict.fromkeys(topics)) if all(topics) else []


def _agenda_text(flag: RiskFlag) -> tuple[str, str]:
    code = flag.rule_code
    explanation = flag.explanation or ""
    # R1, R3 and R6 explanations open with whose answer triggered them.
    from_mentor = explanation.startswith(("Наставник", "Оценки наставника"))

    if code == "R1":
        question = "Что в этом месяце прошло хорошо, а что хотелось бы улучшить?"
        if from_mentor:
            return question, "Наставник поставил высокую оценку, но в комментарии есть замечание"
        return question, "Стажёр поставил высокую оценку, но в комментарии есть замечание"
    if code == "R2":
        return (
            "Каким был для вас этот месяц и что стоит прояснить вместе с наставником?",
            "Стажёр и наставник по-разному описали месяц",
        )
    if code == "R3":
        return (
            "Что изменилось за последние месяцы и что помогло бы сейчас?",
            "Оценки наставника снижаются несколько месяцев" if from_mentor
            else "Оценки снижаются несколько месяцев",
        )
    if code == "R4":
        return (
            "Как до вас доходят приглашения на опрос и как было бы удобнее делиться впечатлениями?",
            "Нет ответа на ежемесячный опрос",
        )
    if code == "R5":
        topics = _repeated_topics(explanation)
        if "leave" in topics:
            return _CAREFUL_QUESTION, _CAREFUL_WHY
        if not topics:
            return _REPEATED_QUESTION, "Одна и та же трудность повторяется в комментариях"
        if len(topics) > 1:
            labels = ", ".join(TOPIC_LABELS[topic] for topic in topics)
            return _REPEATED_QUESTION, f"В комментариях повторяются темы: {labels}"
        topic, = topics
        return (
            _TOPIC_QUESTIONS.get(topic, _REPEATED_QUESTION),
            f"В комментариях повторяется тема «{TOPIC_LABELS[topic]}»",
        )
    if code == "R6":
        if from_mentor:
            return (
                "Какой поддержки, времени или информации вам не хватало в этом месяце?",
                "Наставник поставил низкую оценку месяца",
            )
        return (
            "Что в этом месяце давалось труднее всего и что могло бы помочь?",
            "Низкая оценка месяца от стажёра и наставника"
            if "Наставник поставил" in explanation else "Стажёр поставил низкую оценку месяца",
        )
    if code == "R7":
        return _CAREFUL_QUESTION, _CAREFUL_WHY
    return _UNKNOWN_QUESTION, _UNKNOWN_REASON


def build_agenda(flags: Iterable[RiskFlag]) -> list[AgendaItem]:
    """Up to five distinct questions for a 1:1, the most severe signals first."""
    items: dict[str, AgendaItem] = {}
    for flag in sorted(flags, key=lambda item: (severity_rank(item.severity), rule_rank(item.rule_code))):
        question, why = _agenda_text(flag)
        items.setdefault(question, AgendaItem(rule_code=flag.rule_code, question=question, why=why))
    return list(items.values())[:MAX_AGENDA_ITEMS]
