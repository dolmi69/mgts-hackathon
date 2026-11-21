from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from ..config import settings
from ..schemas import CanonicalAnswer
from ..utils import parse_period

YANDEX_SOURCES = ("yandex", "yandex_forms", "yandexforms")


class AdapterError(ValueError):
    pass


def normalize_source(source: str) -> str:
    if source.lower().replace("-", "_") in YANDEX_SOURCES:
        return "yandex_forms"
    return source


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _flatten_fields(payload: dict[str, Any]) -> dict[str, str]:
    """Best-effort extraction from different Yandex Forms webhook shapes."""
    fields: dict[str, str] = {}

    def add_field(key: object, value: str) -> None:
        name = str(key).strip().lower()
        if name in fields and fields[name] != value:
            # Conflicting titles need an explicit field ID.
            fields[name] = ""
        else:
            fields[name] = value

    for key, value in payload.items():
        if isinstance(value, (str, int, float)) and key not in {"answers", "fields", "data"}:
            add_field(key, str(value))

    raw_answers = payload.get("answers") or payload.get("fields") or payload.get("data")
    if isinstance(raw_answers, dict):
        for key, value in raw_answers.items():
            if isinstance(value, (str, int, float)):
                add_field(key, str(value))
            elif isinstance(value, list):
                add_field(key, ", ".join(str(item) for item in value))
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
            if value is not None:
                for key in (label, item.get("id"), item.get("key")):
                    if key is not None:
                        add_field(key, str(value))
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
    match = re.fullmatch(r"([1-5](?:[.,]\d+)?)(?:\s*(?:/|из)\s*5)?", value.strip())
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    return number if 1 <= number <= 5 else None


def _overall(fields: dict[str, str], field_id: str | None) -> float:
    if field_id is not None:
        score = _number(fields.get(field_id.strip().lower(), ""))
        if score is None:
            raise AdapterError("В настроенном поле общей оценки требуется число от 1 до 5")
        return score

    explicit = []
    for key, value in fields.items():
        label = re.sub(r"[_-]+", " ", key).replace("ё", "е")
        separate_score = any(part in label for part in ("нагруз", "workload", "наставнич", "mentorship"))
        if not separate_score and (
            label in {"overall", "overall score", "overall rating", "общая", "удовлетворенность"}
            or label.startswith(("общая оценка", "общий балл", "общая удовлетворенность"))
        ):
            explicit.append(value)
    candidates = explicit or [fields[label] for label in ("оценка", "score", "rating") if label in fields]
    scores = {_number(value) for value in candidates}
    if None in scores or not scores:
        raise AdapterError("Не удалось найти общую оценку 1–5; укажите поле общей оценки")
    if len(scores) != 1:
        raise AdapterError("Общая оценка неоднозначна; укажите поле общей оценки")
    return scores.pop()


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

    mapping = settings.yandex_forms_fields.get(str(form_id), {})

    def pick(field: str, *fallback: str) -> str:
        if field in mapping:
            return fields.get(mapping[field].strip().lower(), "").strip()
        return next((value for value in fallback if value), "")

    respondent_raw = mapping.get("respondent_type") or pick(
        "respondent",
        fields.get("respondent_type", ""),
        _find_any(fields, ("роль", "кто заполнил", "respondent")),
    )
    respondent_type = "intern"
    if "настав" in respondent_raw.lower() or "mentor" in respondent_raw.lower():
        respondent_type = "mentor"
    elif respondent_raw or "respondent" in mapping:
        if not any(part in respondent_raw.lower() for part in ("стаж", "intern", "участ")):
            raise AdapterError("Не удалось определить роль отвечающего (стажёр/наставник)")

    intern_id = pick(
        "intern_id",
        _find(fields, ("стаж", "id")),
        _find(fields, ("стаж", "email")),
        _find(fields, ("intern", "id")),
        _find(fields, ("intern", "email")),
    )
    mentor_id = pick(
        "mentor_id",
        _find(fields, ("настав", "id")),
        _find(fields, ("настав", "email")),
        _find(fields, ("mentor", "id")),
        _find(fields, ("mentor", "email")),
    )

    if not intern_id and respondent_type == "intern" and "intern_id" not in mapping:
        intern_id = fields.get("email", "").strip() or fields.get("почта", "").strip()

    period_raw = pick("period", _find_any(fields, ("период", "месяц", "period", "month")))
    if "period" in mapping and not period_raw:
        raise AdapterError("В ответе отсутствует настроенное поле отчётного месяца")
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

    overall = _overall(fields, mapping.get("overall"))
    workload = _number(pick("workload", _find_any(fields, ("нагруз", "workload"))))
    mentorship = _number(pick("mentorship", _find_any(fields, ("наставнич", "mentorship"))))
    comment = pick(
        "comment",
        _find_any(fields, ("коммент", "пожелан", "comment", "feedback")),
        _find_any(fields, ("текст",)),
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
    if normalize_source(source) == "yandex_forms":
        return parse_yandex_forms(payload)
    return CanonicalAnswer.model_validate(payload)
