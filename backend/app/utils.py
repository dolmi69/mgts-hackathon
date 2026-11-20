from __future__ import annotations

import re
from datetime import date

RU_MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)

RU_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def format_period(value: date | None) -> str:
    if value is None:
        return "—"
    return f"{RU_MONTHS[value.month - 1]} {value.year}"


def format_period_short(value: date | None) -> str:
    if value is None:
        return "—"
    return f"{RU_MONTHS_GENITIVE[value.month - 1]} {value.year}"


def period_key(value: date) -> str:
    return value.strftime("%Y-%m")


def parse_period(value: object) -> date:
    """Parse common period representations into the first day of a month."""
    if isinstance(value, date):
        return value.replace(day=1)
    if value is None:
        raise ValueError("period is required")
    raw = str(value).strip()
    if not raw:
        raise ValueError("period is required")

    iso_match = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", raw)
    if iso_match:
        return date(int(iso_match.group(1)), int(iso_match.group(2)), 1)

    dotted_match = re.fullmatch(r"(\d{1,2})[./](\d{4})", raw)
    if dotted_match:
        return date(int(dotted_match.group(2)), int(dotted_match.group(1)), 1)

    full_dotted = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", raw)
    if full_dotted:
        return date(int(full_dotted.group(3)), int(full_dotted.group(2)), 1)

    try:
        return date.fromisoformat(raw[:10]).replace(day=1)
    except ValueError as exc:
        raise ValueError(f"unsupported period format: {value!r}") from exc


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def severity_rank(value: str | None) -> int:
    return _SEVERITY_ORDER.get(value or "", 3)


def truncate(text: str | None, limit: int = 220) -> str:
    if not text:
        return ""
    clean = " ".join(str(text).split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def month_index(start: date, current: date) -> int:
    return (current.year - start.year) * 12 + current.month - start.month
