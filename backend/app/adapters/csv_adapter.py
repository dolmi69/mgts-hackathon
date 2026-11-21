from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from typing import Any

from ..schemas import CanonicalAnswer
from ..utils import parse_period


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_header(value: str) -> str:
    text = (value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", "_", text)
    return text.strip("_")


def _delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _aliases(*values: str) -> set[str]:
    return {_normalize_header(value) for value in values}


ANSWER_ALIASES: dict[str, set[str]] = {
    "external_answer_id": _aliases(
        "external_answer_id", "answer_id", "id", "номер_ответа", "id_ответа"
    ),
    "form_id": _aliases("form_id", "id_формы", "форма"),
    "period": _aliases("period", "month", "месяц", "период", "отчетный_месяц"),
    "respondent_type": _aliases(
        "respondent_type", "role", "роль", "кто", "тип_респондента"
    ),
    "intern_external_id": _aliases(
        "intern_external_id", "intern_id", "стажер", "стажёр", "id_стажера", "email"
    ),
    "mentor_external_id": _aliases(
        "mentor_external_id", "mentor_id", "наставник", "id_наставника"
    ),
    "overall": _aliases(
        "overall", "overall_score", "общая_оценка", "удовлетворенность"
    ),
    "workload": _aliases("workload", "нагрузка"),
    "mentorship": _aliases("mentorship", "наставничество"),
    "comment": _aliases("comment", "комментарий", "текст", "пожелания"),
    "submitted_at": _aliases(
        "submitted_at", "created_at", "дата", "дата_ответа", "timestamp"
    ),
}

INTERN_ALIASES: dict[str, set[str]] = {
    "external_id": _aliases(
        "external_id", "intern_id", "id", "стажер", "стажёр", "email", "id_стажера"
    ),
    "name": _aliases("name", "фио", "имя", "стажер", "стажёр"),
    "direction": _aliases("direction", "направление", "команда", "отдел"),
    "mentor_external_id": _aliases(
        "mentor_external_id", "mentor_id", "наставник", "id_наставника"
    ),
    "start_period": _aliases("start_period", "start", "начало", "старт", "месяц_старта"),
    "status": _aliases("status", "статус"),
}


def _pick(row: dict[str, str], aliases: set[str], default: str = "") -> str:
    for key, value in row.items():
        if key in aliases:
            return (value or "").strip()
    return default


def _parse_float(value: str, field: str) -> float:
    text = (value or "").strip().replace(",", ".").replace("%", "")
    if not text:
        raise ValueError(f"пустое поле {field}")
    number = float(text)
    if not 1 <= number <= 5:
        raise ValueError(f"{field} должна быть от 1 до 5")
    return number


def _respondent_type(value: str) -> str:
    text = (value or "").strip().lower()
    if "настав" in text or "mentor" in text:
        return "mentor"
    if "стаж" in text or "intern" in text or "участ" in text:
        return "intern"
    raise ValueError("не удалось определить respondent_type (стажёр/наставник)")


def _auto_id(row: dict[str, str], period: str, respondent: str, intern: str) -> str:
    payload = "|".join(
        [
            period,
            respondent,
            intern,
            _pick(row, ANSWER_ALIASES["comment"]),
            _pick(row, ANSWER_ALIASES["overall"] | {"оценка"}),
        ]
    )
    return "csv-" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def parse_answers_csv(data: bytes) -> tuple[list[CanonicalAnswer], list[str]]:
    text = _decode(data)
    if not text.strip():
        return [], ["Файл ответов пуст"]
    reader = csv.DictReader(io.StringIO(text), delimiter=_delimiter(text))
    answers: list[CanonicalAnswer] = []
    errors: list[str] = []
    for row_number, raw_row in enumerate(reader, start=2):
        try:
            row = {_normalize_header(key): value for key, value in raw_row.items() if key}
            period_raw = _pick(row, ANSWER_ALIASES["period"])
            period = parse_period(period_raw)
            respondent = _respondent_type(_pick(row, ANSWER_ALIASES["respondent_type"]))
            intern_id = _pick(row, ANSWER_ALIASES["intern_external_id"])
            if not intern_id:
                raise ValueError("не указан стажёр")
            explicit = [
                value for key, value in row.items()
                if key in ANSWER_ALIASES["overall"] and value and value.strip()
            ]
            overall_values = {_parse_float(value, "overall") for value in explicit or [row.get("оценка")]}
            if len(overall_values) != 1:
                raise ValueError("общая оценка неоднозначна")
            overall = overall_values.pop()
            scores: dict[str, Any] = {"overall": overall}
            workload = _pick(row, ANSWER_ALIASES["workload"])
            mentorship = _pick(row, ANSWER_ALIASES["mentorship"])
            if workload:
                scores["workload"] = _parse_float(workload, "workload")
            if mentorship:
                scores["mentorship"] = _parse_float(mentorship, "mentorship")
            external_id = _pick(row, ANSWER_ALIASES["external_answer_id"]) or _auto_id(
                row, period_raw, respondent, intern_id
            )
            answers.append(
                CanonicalAnswer(
                    source="csv",
                    external_answer_id=external_id,
                    form_id=_pick(row, ANSWER_ALIASES["form_id"]) or None,
                    period=period,
                    respondent_type=respondent,
                    intern_external_id=intern_id,
                    mentor_external_id=_pick(row, ANSWER_ALIASES["mentor_external_id"]) or None,
                    scores=scores,
                    comment=_pick(row, ANSWER_ALIASES["comment"]),
                    submitted_at=_pick(row, ANSWER_ALIASES["submitted_at"]) or None,
                    raw={key: value for key, value in row.items()},
                )
            )
        except Exception as exc:  # noqa: BLE001 - report row-level errors
            errors.append(f"Ответы, строка {row_number}: {exc}")
    return answers, errors


def parse_interns_csv(data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    text = _decode(data)
    if not text.strip():
        return [], ["Файл стажёров пуст"]
    reader = csv.DictReader(io.StringIO(text), delimiter=_delimiter(text))
    interns: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, raw_row in enumerate(reader, start=2):
        try:
            row = {_normalize_header(key): value for key, value in raw_row.items() if key}
            external_id = _pick(row, INTERN_ALIASES["external_id"])
            if not external_id:
                raise ValueError("не указан external_id")
            start_raw = _pick(row, INTERN_ALIASES["start_period"])
            start_period: date | None = parse_period(start_raw) if start_raw else None
            interns.append(
                {
                    "external_id": external_id,
                    "name": _pick(row, INTERN_ALIASES["name"]) or external_id,
                    "direction": _pick(row, INTERN_ALIASES["direction"]) or None,
                    "mentor_external_id": _pick(row, INTERN_ALIASES["mentor_external_id"])
                    or None,
                    "start_period": start_period,
                    "status": _pick(row, INTERN_ALIASES["status"]) or "active",
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Стажёры, строка {row_number}: {exc}")
    return interns, errors
