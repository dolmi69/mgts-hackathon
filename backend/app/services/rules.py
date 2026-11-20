from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Answer, Intern, RiskFlag
from ..utils import (
    add_months,
    format_period,
    format_period_short,
    severity_rank,
    truncate,
)

DEFAULT_RULES: dict[str, Any] = {
    "program_months": 6,
    "r1": {"high_score": 4.0},
    "r2": {"gap": 2.0},
    "r3": {"slope": -0.4, "total_drop": 2.0, "latest_low": 2.0},
    "r4": {"grace_days": 3},
    "r5": {"min_repeats": 2, "window": 3},
    "r6": {"low_score": 2.0, "enabled": True},
}

TOPIC_LABELS = {
    "workload": "нагрузка",
    "tasks": "задачи",
    "mentorship": "наставничество",
    "team": "команда",
    "process": "процессы",
    "growth": "развитие",
    "other": "другое",
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_rules_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or settings.rules_path
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return deepcopy(DEFAULT_RULES)
    return _merge(DEFAULT_RULES, raw)


def _fmt_score(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _fmt_slope(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _analysis(answer: Answer):
    analysis = answer.analysis
    if analysis is not None and analysis.status == "done":
        return analysis
    return None


def _quote(answer: Answer) -> str:
    analysis = _analysis(answer)
    if analysis is not None and analysis.evidence:
        return truncate(analysis.evidence[0], 180)
    return truncate(answer.comment, 180) or "комментарий пуст"


def _opposite_sentiment(first: Answer, second: Answer) -> bool:
    a = _analysis(first)
    b = _analysis(second)
    if a is None or b is None:
        return False
    left = a.sentiment
    right = b.sentiment
    return (left == "positive" and right in {"negative"}) or (
        right == "positive" and left in {"negative"}
    )


def evaluate_intern(
    intern: Intern,
    answers: list[Answer],
    as_of: date,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate deterministic rules for one intern.

    Returns plain dicts so the function is easy to test without a database.
    """
    cfg = config or load_rules_config()
    answers = sorted(answers, key=lambda item: (item.period, item.respondent_type))
    by_period: dict[date, dict[str, Answer]] = {}
    for answer in answers:
        by_period.setdefault(answer.period, {})
        by_period[answer.period].setdefault(answer.respondent_type, answer)
    periods = sorted(by_period)
    if not periods:
        return []

    flags: dict[tuple[date, str], dict[str, Any]] = {}

    def add_flag(
        period: date,
        code: str,
        severity: str,
        title: str,
        explanation: str,
        evidence_ids: list[int] | None = None,
    ) -> None:
        key = (period, code)
        existing = flags.get(key)
        if existing is None:
            flags[key] = {
                "period": period,
                "rule_code": code,
                "severity": severity,
                "title": title,
                "explanation": explanation,
                "evidence_answer_ids": list(evidence_ids or []),
            }
            return
        if severity_rank(severity) < severity_rank(existing["severity"]):
            existing["severity"] = severity
        if explanation and explanation not in existing["explanation"]:
            existing["explanation"] += " " + explanation
        for answer_id in evidence_ids or []:
            if answer_id not in existing["evidence_answer_ids"]:
                existing["evidence_answer_ids"].append(answer_id)

    # R1: high score but a complaint or mixed/negative comment.
    high_score = float(cfg["r1"]["high_score"])
    for period in periods:
        for respondent_type, who in (("intern", "Стажёр"), ("mentor", "Наставник")):
            answer = by_period[period].get(respondent_type)
            if answer is None or answer.overall_score < high_score:
                continue
            analysis = _analysis(answer)
            if analysis is None:
                continue
            has_signal = analysis.sentiment in {"negative", "mixed"} or bool(
                analysis.problem_topics
            )
            if not has_signal:
                continue
            quote = _quote(answer)
            add_flag(
                period,
                "R1",
                "medium",
                "Высокая оценка, но жалоба в комментарии",
                f"{who} поставил оценку {_fmt_score(answer.overall_score)}, "
                f"но в комментарии есть сигнал: «{quote}».",
                [answer.id],
            )

    # R2: intern and mentor see the same month differently.
    gap_threshold = float(cfg["r2"]["gap"])
    for period in periods:
        intern_answer = by_period[period].get("intern")
        mentor_answer = by_period[period].get("mentor")
        if intern_answer is None or mentor_answer is None:
            continue
        gap = abs(intern_answer.overall_score - mentor_answer.overall_score)
        opposite = _opposite_sentiment(intern_answer, mentor_answer)
        if gap < gap_threshold and not opposite:
            continue
        severity = "high" if gap >= gap_threshold else "medium"
        quote = _quote(mentor_answer)
        add_flag(
            period,
            "R2",
            severity,
            "Стажёр и наставник видят месяц по-разному",
            f"Стажёр оценил месяц на {_fmt_score(intern_answer.overall_score)}, "
            f"наставник — на {_fmt_score(mentor_answer.overall_score)} "
            f"(разница {_fmt_score(gap)}). Комментарий наставника: «{quote}».",
            [intern_answer.id, mentor_answer.id],
        )

    # R3: a sustained downward trend, not a single noisy month.
    for respondent_type, who in (("intern", "стажёра"), ("mentor", "наставника")):
        series = [
            by_period[period][respondent_type]
            for period in periods
            if respondent_type in by_period[period]
        ]
        if len(series) < 3:
            continue
        window = series[-6:]
        scores = [float(item.overall_score) for item in window]
        total_drop = scores[0] - scores[-1]
        drops = sum(
            1 for index in range(1, len(scores)) if scores[index] < scores[index - 1]
        )
        count = len(scores)
        mean_x = (count - 1) / 2
        mean_y = sum(scores) / count
        denominator = sum((index - mean_x) ** 2 for index in range(count))
        slope = (
            sum(
                (index - mean_x) * (score - mean_y)
                for index, score in enumerate(scores)
            )
            / denominator
            if denominator
            else 0.0
        )
        # One small dip is not a trend: require a real decline and at least
        # two lower months (or a loss of two points overall).
        if total_drop <= 0 or slope >= float(cfg["r3"]["slope"]):
            continue
        if drops < 2 and total_drop < float(cfg["r3"]["total_drop"]):
            continue
        latest = scores[-1]
        severity = (
            "high"
            if latest <= float(cfg["r3"]["latest_low"])
            or total_drop >= float(cfg["r3"]["total_drop"])
            else "medium"
        )
        first_item, last_item = window[0], window[-1]
        add_flag(
            last_item.period,
            "R3",
            severity,
            "Оценки снижаются несколько месяцев",
            f"Оценки {who} снижаются с {format_period_short(first_item.period)} "
            f"({_fmt_score(scores[0])}) до {format_period_short(last_item.period)} "
            f"({_fmt_score(latest)}); наклон {_fmt_slope(slope)} балла в месяц.",
            [item.id for item in window],
        )

    # R4: an expected monthly survey is missing.
    start = intern.start_period or periods[0]
    expected = [
        add_months(start, index) for index in range(int(cfg["program_months"]))
    ]
    expected = [period for period in expected if period <= as_of]
    present_intern = {
        period for period, values in by_period.items() if "intern" in values
    }
    present_mentor = {
        period for period, values in by_period.items() if "mentor" in values
    }
    missing = [period for period in expected if period not in present_intern]
    missing_set = set(missing)
    for period in missing:
        consecutive = 1
        previous = add_months(period, -1)
        while previous in missing_set:
            consecutive += 1
            previous = add_months(previous, -1)
        severity = "high" if consecutive >= 2 else "medium"
        details = f"Нет ответа стажёра за {format_period(period)}."
        if consecutive >= 2:
            details += f" Это {consecutive}-й пропуск подряд."
        if period not in present_mentor:
            details += " Ответ наставника за этот месяц тоже отсутствует."
        add_flag(
            period,
            "R4",
            severity,
            "Пропущен ежемесячный опрос",
            details,
            [],
        )

    # R5: the same problem topic repeats in recent intern comments.
    window = int(cfg["r5"]["window"])
    min_repeats = int(cfg["r5"]["min_repeats"])
    intern_series = [
        by_period[period]["intern"] for period in periods if "intern" in by_period[period]
    ]
    recent = intern_series[-window:]
    topic_answers: dict[str, list[Answer]] = {}
    for answer in recent:
        analysis = _analysis(answer)
        if analysis is None:
            continue
        for topic in analysis.problem_topics:
            topic_answers.setdefault(topic, []).append(answer)
    if recent:
        for topic, topic_answer_list in topic_answers.items():
            if len(topic_answer_list) < min_repeats:
                continue
            label = TOPIC_LABELS.get(topic, topic)
            example = _quote(topic_answer_list[-1])
            add_flag(
                recent[-1].period,
                "R5",
                "medium",
                "Повторяющаяся проблема",
                f"Тема «{label}» повторяется в {len(topic_answer_list)} из "
                f"{len(recent)} последних комментариев стажёра. Пример: «{example}».",
                [item.id for item in topic_answer_list],
            )

    # R6: a standalone low score. Optional, enabled in rules.json.
    if cfg["r6"].get("enabled", True):
        low_score = float(cfg["r6"]["low_score"])
        for period in periods:
            for respondent_type, who in (("intern", "Стажёр"), ("mentor", "Наставник")):
                answer = by_period[period].get(respondent_type)
                if answer is None or answer.overall_score > low_score:
                    continue
                severity = "high" if answer.overall_score <= 1.5 else "medium"
                add_flag(
                    period,
                    "R6",
                    severity,
                    "Низкая оценка месяца",
                    f"{who} поставил оценку {_fmt_score(answer.overall_score)} "
                    f"за {format_period(period)}.",
                    [answer.id],
                )

    result = list(flags.values())
    result.sort(key=lambda item: (item["period"], severity_rank(item["severity"]), item["rule_code"]))
    return result


def recompute_flags(db: Session) -> int:
    """Recompute all flags. Idempotent and intentionally simple for the MVP."""
    interns = db.scalars(select(Intern)).all()
    answers = db.scalars(
        select(Answer).options(selectinload(Answer.analysis)).order_by(Answer.period)
    ).all()
    if not answers:
        db.execute(delete(RiskFlag))
        db.commit()
        return 0

    as_of = max(answer.period for answer in answers)
    config = load_rules_config()
    by_intern: dict[int, list[Answer]] = {}
    for answer in answers:
        by_intern.setdefault(answer.intern_id, []).append(answer)

    new_flags: list[RiskFlag] = []
    for intern in interns:
        intern_answers = by_intern.get(intern.id, [])
        if not intern_answers:
            continue
        for item in evaluate_intern(intern, intern_answers, as_of, config):
            new_flags.append(
                RiskFlag(
                    intern_id=intern.id,
                    period=item["period"],
                    rule_code=item["rule_code"],
                    severity=item["severity"],
                    title=item["title"],
                    explanation=item["explanation"],
                    evidence_answer_ids=item["evidence_answer_ids"],
                )
            )

    db.execute(delete(RiskFlag))
    db.add_all(new_flags)
    db.commit()
    return len(new_flags)
