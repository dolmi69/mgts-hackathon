from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from ..schemas import CanonicalAnswer
from ..utils import parse_period


class AdapterError(ValueError):
    pass


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _flatten_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Best-effort extraction from different Yandex Forms webhook shapes."""
    fields: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float)) and key not in {"answers", "fields", "data"}:
            fields[str(key).lower()] = str(value)

    raw_answers = payload.get("answers") or payload.get("fields") or payload.get("data")
    if isinstance(raw_answers, dict):
        for key, value in raw_answers.items():
            if isinstance(value, (str, int, float)):
                fields[str(key).lower()] = str(value)
            elif isinstance(value, list):
                fields[str(key).lower()] = ", ".join(str(item) for item in value)
    elif isinstance(raw_answers, list):
        for item in raw_answers:
            if not isinstance(item, dict):
                continue
            label = (
                item.get("question")
                or item.get("label")
                or item.get("name")
                or item.get("title")
                or item.get("key")
            )
            value = item.get("value", item.get("answer", item.get("text")))
            if label is not None and value is not None:
                fields[str(label).lower()] = str(value)
    return fields


def _find(fields: dict[str, str], parts: tuple[str, ...]) -> str:
    for key, value in fields.items():
        if all(part in key for part in parts):
            return value.strip()
    return ""


def _find_any(fields: dict[str, str], parts: tuple[str, ...]) -> str:
    for key, value in fields.items():
        if any(part in key for part in parts):
            return value.strip()
    return ""


def _number(value: str) -> float | None:
    text = (value or "").replace(",", ".").strip()
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    return number if 1 <= number <= 5 else None


def parse_yandex_forms(payload: dict[str, Any]) -> CanonicalAnswer:
    fields = _flatten_fields(payload)
    if not fields:
        raise AdapterError("Пустой payload Яндекс Форм")

    answer_id = str(
        _first(payload, ("answer_id", "response_id", "id"))
        or fields.get("answer_id")
        or fields.get("id")
        or ""
    )
    created = (
        _first(payload, ("created", "created_at", "submitted_at", "date"))
        or fields.get("created")
        or fields.get("дата")
        or fields.get("submitted_at")
    )
    form_id = _first(payload, ("form_id", "form"))
    if not form_id:
        form_id = fields.get("form_id")

    respondent_raw = _find_any(fields, ("роль", "кто", "respondent", "type", "категория"))
    respondent_type = "intern"
    if "настав" in respondent_raw.lower() or "mentor" in respondent_raw.lower():
        respondent_type = "mentor"

    intern_id = (
        _find(fields, ("стаж", "id"))
        or _find(fields, ("стаж", "email"))
        or _find(fields, ("intern", "id"))
        or _find(fields, ("intern", "email"))
        or _find_any(fields, ("email", "почта"))
    )
    mentor_id = (
        _find(fields, ("настав", "id"))
        or _find(fields, ("настав", "email"))
        or _find(fields, ("mentor", "id"))
        or _find(fields, ("mentor", "email"))
    )

    if not intern_id and respondent_type == "mentor":
        intern_id = _find_any(fields, ("стаж", "фио")) or _find_any(fields, ("стаж", "имя"))

    period_raw = _find_any(fields, ("период", "месяц", "period", "month"))
    if period_raw:
        period = parse_period(period_raw)
    elif created:
        period = parse_period(str(created)[:10])
    else:
        period = date.today().replace(day=1)

    if not intern_id:
        raise AdapterError("Не удалось найти стажёра в ответе. Нужен ID/email стажёра.")
    if not answer_id:
        answer_id = "yf-" + hashlib.sha1(
            repr(sorted(fields.items())).encode("utf-8")
        ).hexdigest()[:20]

    overall = None
    for key, value in fields.items():
        if any(marker in key for marker in ("общ", "удовлетвор", "overall", "оценка")):
            overall = _number(value)
            if overall is not None:
                break
    if overall is None:
        for value in fields.values():
            overall = _number(value)
            if overall is not None:
                break
    if overall is None:
        raise AdapterError("Не удалось найти оценку 1–5 в ответе")

    workload = _number(_find_any(fields, ("нагруз", "workload")))
    mentorship = _number(_find_any(fields, ("наставнич", "mentorship", "ментор")))
    comment = (
        _find_any(fields, ("коммент", "пожелан", "comment", "feedback"))
        or _find_any(fields, ("текст", "что"))
    )

    scores: dict[str, Any] = {"overall": overall}
    if workload is not None:
        scores["workload"] = workload
    if mentorship is not None:
        scores["mentorship"] = mentorship

    return CanonicalAnswer(
        source="yandex_forms",
        external_answer_id=answer_id,
        form_id=str(form_id) if form_id else None,
        period=period,
        respondent_type=respondent_type,
        intern_external_id=intern_id,
        mentor_external_id=mentor_id or None,
        scores=scores,
        comment=comment,
        submitted_at=str(created) if created else None,
        raw=payload,
    )


def parse_payload(source: str, payload: dict[str, Any]) -> CanonicalAnswer:
    normalized = source.lower().replace("-", "_")
    if normalized in {"yandex", "yandex_forms", "yandexforms"}:
        return parse_yandex_forms(payload)
    return CanonicalAnswer.model_validate(payload)
