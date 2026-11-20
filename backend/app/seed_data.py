from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .adapters.csv_adapter import parse_answers_csv, parse_interns_csv
from .config import settings
from .models import AIAnalysis, Answer, IngestEvent, Intern, Mentor, RiskFlag, SyncRun
from .services.analysis import analyze_pending
from .services.ingest import get_or_create_intern, get_or_create_mentor, upsert_answer
from .services.rules import recompute_flags
from .utils import add_months

PERIOD_START = date(2025, 10, 1)
PERIOD_COUNT = 6
PERIODS = [add_months(PERIOD_START, index) for index in range(PERIOD_COUNT)]

MENTORS = [
    {"external_id": "men-01", "name": "Олег Кузнецов"},
    {"external_id": "men-02", "name": "Ирина Васильева"},
    {"external_id": "men-03", "name": "Сергей Павлов"},
    {"external_id": "men-04", "name": "Наталья Громова"},
]

INTERNS = [
    {
        "external_id": "int-001",
        "name": "Анна Смирнова",
        "direction": "Продуктовая аналитика",
        "mentor_external_id": "men-01",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-002",
        "name": "Илья Петров",
        "direction": "Backend-разработка",
        "mentor_external_id": "men-01",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-003",
        "name": "Мария Козлова",
        "direction": "Мобильная разработка",
        "mentor_external_id": "men-01",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-004",
        "name": "Дмитрий Волков",
        "direction": "Инфраструктура",
        "mentor_external_id": "men-01",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-005",
        "name": "Елена Соколова",
        "direction": "Продукт",
        "mentor_external_id": "men-02",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-006",
        "name": "Артём Лебедев",
        "direction": "Data Science",
        "mentor_external_id": "men-02",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-007",
        "name": "Ольга Новикова",
        "direction": "Маркетинг",
        "mentor_external_id": "men-02",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-008",
        "name": "Павел Морозов",
        "direction": "Аналитика",
        "mentor_external_id": "men-03",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-009",
        "name": "Софья Зайцева",
        "direction": "Дизайн",
        "mentor_external_id": "men-03",
        "start_period": date(2026, 1, 1),
    },
    {
        "external_id": "int-010",
        "name": "Никита Егоров",
        "direction": "Frontend-разработка",
        "mentor_external_id": "men-03",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-011",
        "name": "Алиса Тимофеева",
        "direction": "Поддержка продуктов",
        "mentor_external_id": "men-04",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-012",
        "name": "Максим Фёдоров",
        "direction": "Тестирование",
        "mentor_external_id": "men-04",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-013",
        "name": "Виктория Орлова",
        "direction": "HR-аналитика",
        "mentor_external_id": "men-04",
        "start_period": date(2025, 10, 1),
    },
    {
        "external_id": "int-014",
        "name": "Роман Белов",
        "direction": "Интеграции",
        "mentor_external_id": "men-04",
        "start_period": date(2025, 10, 1),
    },
]


def _intern_comment(score: float) -> str:
    if score >= 5:
        return "Интересные задачи, наставник помогает, всё нравится."
    if score >= 4:
        return "В целом всё хорошо: задачи понятные, команда поддерживает."
    if score >= 3:
        return "Есть интересные задачи, но не хватает обратной связи от наставника."
    if score >= 2:
        return "Слишком много задач, не хватает поддержки и времени на обучение."
    return "Очень тяжело, не хватает поддержки."


def _mentor_comment(score: float) -> str:
    if score >= 5:
        return "Стажёр хорошо справляется, проявляет инициативу."
    if score >= 4:
        return "Прогресс есть, нужно больше самостоятельности."
    if score >= 3:
        return "Есть сложности с приоритизацией, помогаем."
    if score >= 2:
        return "Нужна поддержка, не хватает самостоятельности."
    return "Пока много сложностей, нужен плотный контроль."


# Scores and optional comment overrides by period index.
# None in a score list means the survey was not submitted.
PROFILES: dict[str, dict[str, Any]] = {
    "int-001": {
        "intern_scores": [5, 5, 4, 5, 4, 5],
        "mentor_scores": [5, 4, 5, 4, 5, 5],
        "intern_comments": {
            3: "Всё нравится, но задач стало слишком много.",
            4: "Всё нравится, но нагрузка выросла.",
            5: "Отличная команда, но задач слишком много.",
        },
    },
    "int-002": {
        "intern_scores": [5, 5, 4, 4, 3, 3],
        "mentor_scores": [3, 3, 3, 2, 2, 2],
        "intern_comments": {
            4: "Есть интересные задачи, но не хватает обратной связи.",
            5: "Задачи нравятся, но поддержки от наставника не хватает.",
        },
        "mentor_comments": {
            0: "Не хватает самостоятельности, нужно больше инициативы.",
            1: "Не хватает самостоятельности, нужно больше инициативы.",
            2: "Не хватает самостоятельности, нужно больше инициативы.",
            3: "Не хватает самостоятельности и вовлечённости.",
            4: "Не хватает самостоятельности и вовлечённости.",
            5: "Не хватает самостоятельности и вовлечённости.",
        },
    },
    "int-003": {
        "intern_scores": [5, 4, 4, 3, 3, 2],
        "mentor_scores": [4, 4, 3, 3, 2, 2],
        "intern_comments": {
            1: "Задач стало больше, но пока справляюсь.",
            2: "Не хватает времени на обучение, задач много.",
            3: "Слишком много задач и мало обратной связи.",
            4: "Очень тяжело, не хватает поддержки.",
            5: "Не хватает поддержки, думаю о выходе.",
        },
        "mentor_comments": {
            3: "Нужна поддержка, не хватает самостоятельности.",
            4: "Нужна поддержка, не хватает самостоятельности.",
            5: "Нужна поддержка, не хватает самостоятельности.",
        },
    },
    "int-004": {
        "intern_scores": [5, 5, 4, 4, None, None],
        "mentor_scores": [4, 4, 4, 4, None, None],
    },
    "int-005": {
        "intern_scores": [5, 5, 5, 5, 4, 4],
        "mentor_scores": [5, 4, 5, 4, 4, 4],
        "intern_comments": {
            5: "Всё хорошо, но не хватает обратной связи от наставника.",
        },
    },
    "int-006": {
        "intern_scores": [5, 5, 5, 5, 5, 5],
        "mentor_scores": [5, 5, 5, 4, 5, 5],
        "intern_comments": {
            5: "Отличные задачи, особенно бесконечные правки!",
        },
    },
    "int-007": {
        "intern_scores": [5, 5, 5, 4, 5, 5],
        "mentor_scores": [5, 5, 4, 4, 5, 5],
    },
    "int-008": {
        "intern_scores": [4, 4, 3, 4, 4, 4],
        "mentor_scores": [4, 4, 3, 4, 4, 4],
        "intern_comments": {
            2: "Было тяжело, много задач.",
        },
    },
    "int-009": {
        "intern_scores": [4, 4, 4],
        "mentor_scores": [4, 4, 4],
    },
    "int-010": {
        "intern_scores": [5, 5, 5, 4, 5, 5],
        "mentor_scores": [3, 3, 3, 2, 3, 3],
        "mentor_comments": {
            0: "Не хватает самостоятельности, нужно больше инициативы.",
            1: "Не хватает самостоятельности, нужно больше инициативы.",
            2: "Не хватает самостоятельности, нужно больше инициативы.",
            3: "Не хватает самостоятельности и вовлечённости.",
            4: "Не хватает самостоятельности, нужно больше инициативы.",
            5: "Не хватает самостоятельности, нужно больше инициативы.",
        },
    },
    "int-011": {
        "intern_scores": [4, 3, 3, 3, 3, 3],
        "mentor_scores": [4, 3, 3, 3, 3, 3],
        "intern_comments": {
            3: "Слишком много задач, не хватает времени.",
            4: "Нагрузка слишком большая, не хватает времени.",
            5: "Слишком много задач, не хватает времени на обучение.",
        },
    },
    "int-012": {
        "intern_scores": [4, 4, 2, 4, 4, 4],
        "mentor_scores": [4, 4, 3, 4, 4, 4],
        "intern_comments": {
            2: "Очень тяжело, не хватает поддержки.",
        },
    },
    "int-013": {
        "intern_scores": [4, 4, 4, 4, 4, 4],
        "mentor_scores": [4, 4, 4, 4, 4, 4],
    },
    "int-014": {
        "intern_scores": [5, 4, 5, 4, 5, 4],
        "mentor_scores": [5, 4, 5, 4, 5, 4],
    },
}


def _periods_for_intern(intern: dict[str, Any]) -> list[date]:
    start = intern["start_period"]
    return [period for period in PERIODS if period >= start]


def _clamp(value: float) -> float:
    return max(1.0, min(5.0, value))


def build_seed_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intern_rows: list[dict[str, Any]] = []
    answer_rows: list[dict[str, Any]] = []

    for mentor in MENTORS:
        intern_rows.append(
            {
                "external_id": mentor["external_id"],
                "name": mentor["name"],
                "direction": "",
                "mentor_external_id": "",
                "start_period": "",
                "status": "mentor",
            }
        )

    for intern in INTERNS:
        intern_rows.append(
            {
                "external_id": intern["external_id"],
                "name": intern["name"],
                "direction": intern["direction"],
                "mentor_external_id": intern["mentor_external_id"],
                "start_period": intern["start_period"].strftime("%Y-%m"),
                "status": "active",
            }
        )
        profile = PROFILES.get(intern["external_id"], {})
        periods = _periods_for_intern(intern)
        intern_scores = list(profile.get("intern_scores", [4] * len(periods)))
        mentor_scores = list(profile.get("mentor_scores", [4] * len(periods)))
        # For a late start, trim the leading scores to the actual period count.
        intern_scores = intern_scores[-len(periods) :]
        mentor_scores = mentor_scores[-len(periods) :]
        intern_comments = profile.get("intern_comments", {})
        mentor_comments = profile.get("mentor_comments", {})

        for index, period in enumerate(periods):
            submitted = f"{period.strftime('%Y-%m')}-28 18:00"
            intern_score = intern_scores[index]
            if intern_score is not None:
                comment = intern_comments.get(index) or _intern_comment(float(intern_score))
                answer_rows.append(
                    {
                        "external_answer_id": f"{intern['external_id']}-{period.strftime('%Y-%m')}-intern",
                        "period": period.strftime("%Y-%m-01"),
                        "respondent_type": "intern",
                        "intern_external_id": intern["external_id"],
                        "mentor_external_id": intern["mentor_external_id"],
                        "overall": intern_score,
                        "workload": _clamp(float(intern_score) - (1 if intern_score >= 4 else 0)),
                        "mentorship": _clamp(float(intern_score) + 1),
                        "comment": comment,
                        "submitted_at": submitted,
                    }
                )
            mentor_score = mentor_scores[index]
            if mentor_score is not None:
                comment = mentor_comments.get(index) or _mentor_comment(float(mentor_score))
                answer_rows.append(
                    {
                        "external_answer_id": f"{intern['external_id']}-{period.strftime('%Y-%m')}-mentor",
                        "period": period.strftime("%Y-%m-01"),
                        "respondent_type": "mentor",
                        "intern_external_id": intern["external_id"],
                        "mentor_external_id": intern["mentor_external_id"],
                        "overall": mentor_score,
                        "workload": _clamp(float(mentor_score)),
                        "mentorship": _clamp(float(mentor_score) + 1),
                        "comment": comment,
                        "submitted_at": submitted,
                    }
                )
    return intern_rows, answer_rows


def generate_seed_files(seed_dir: Path | None = None, force: bool = False) -> Path:
    target = Path(seed_dir or settings.seed_dir)
    target.mkdir(parents=True, exist_ok=True)
    interns_path = target / "interns.csv"
    answers_path = target / "answers.csv"
    if interns_path.exists() and answers_path.exists() and not force:
        return target

    intern_rows, answer_rows = build_seed_rows()
    intern_fields = [
        "external_id",
        "name",
        "direction",
        "mentor_external_id",
        "start_period",
        "status",
    ]
    answer_fields = [
        "external_answer_id",
        "period",
        "respondent_type",
        "intern_external_id",
        "mentor_external_id",
        "overall",
        "workload",
        "mentorship",
        "comment",
        "submitted_at",
    ]
    with interns_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=intern_fields, delimiter=";")
        writer.writeheader()
        writer.writerows(intern_rows)
    with answers_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=answer_fields, delimiter=";")
        writer.writeheader()
        writer.writerows(answer_rows)

    cases_path = target / "demo_cases.csv"
    with cases_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["intern_external_id", "name", "case", "expected"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(
            [
                {"intern_external_id": "int-001", "name": "Анна Смирнова", "case": "высокая оценка + жалоба на нагрузку", "expected": "R1"},
                {"intern_external_id": "int-002", "name": "Илья Петров", "case": "стажёр и наставник расходятся", "expected": "R2, R3"},
                {"intern_external_id": "int-003", "name": "Мария Козлова", "case": "падение оценок", "expected": "R3, R6"},
                {"intern_external_id": "int-004", "name": "Дмитрий Волков", "case": "пропущены два опроса", "expected": "R4"},
                {"intern_external_id": "int-005", "name": "Елена Соколова", "case": "вежливый негатив", "expected": "R1"},
                {"intern_external_id": "int-006", "name": "Артём Лебедев", "case": "сарказм", "expected": "R1"},
                {"intern_external_id": "int-007", "name": "Ольга Новикова", "case": "контроль: всё хорошо", "expected": "нет"},
                {"intern_external_id": "int-008", "name": "Павел Морозов", "case": "контроль: один плохой месяц", "expected": "нет"},
                {"intern_external_id": "int-009", "name": "Софья Зайцева", "case": "контроль: новый стажёр", "expected": "нет"},
                {"intern_external_id": "int-010", "name": "Никита Егоров", "case": "наставник видит проблемы", "expected": "R2"},
                {"intern_external_id": "int-011", "name": "Алиса Тимофеева", "case": "повторяющаяся нагрузка", "expected": "R5"},
                {"intern_external_id": "int-012", "name": "Максим Фёдоров", "case": "низкая оценка", "expected": "R6"},
                {"intern_external_id": "int-013", "name": "Виктория Орлова", "case": "контроль: стабильно", "expected": "нет"},
                {"intern_external_id": "int-014", "name": "Роман Белов", "case": "контроль: стабильно", "expected": "нет"},
            ]
        )
    return target


def seed_database(
    db: Session,
    *,
    analyze: bool = True,
    reset: bool = False,
) -> dict[str, int]:
    if reset:
        for model in (RiskFlag, AIAnalysis, SyncRun, IngestEvent, Answer, Intern, Mentor):
            db.execute(delete(model))
        db.commit()

    existing = db.scalar(select(func.count(Answer.id))) or 0
    if existing and not reset:
        return {"skipped": 1, "answers": int(existing)}

    generate_seed_files()
    for mentor in MENTORS:
        get_or_create_mentor(db, mentor["external_id"], mentor["name"])
    db.commit()

    interns_bytes = (settings.seed_dir / "interns.csv").read_bytes()
    answers_bytes = (settings.seed_dir / "answers.csv").read_bytes()
    intern_rows, intern_errors = parse_interns_csv(interns_bytes)
    answer_rows, answer_errors = parse_answers_csv(answers_bytes)

    interns_created = 0
    interns_updated = 0
    for row in intern_rows:
        if row.get("status") == "mentor":
            continue
        _intern, created = get_or_create_intern(
            db,
            row["external_id"],
            name=row["name"],
            direction=row.get("direction"),
            mentor_external_id=row.get("mentor_external_id"),
            start_period=row.get("start_period"),
        )
        interns_created += int(created)
        interns_updated += int(not created)
    db.commit()

    imported = 0
    duplicates = 0
    errors = list(intern_errors) + list(answer_errors)
    for canonical in answer_rows:
        canonical = canonical.model_copy(update={"source": "seed"})
        _answer, created = upsert_answer(db, canonical)
        imported += int(created)
        duplicates += int(not created)
    db.commit()

    pending = len(
        db.scalars(select(Answer.id).where(Answer.analysis_status == "pending")).all()
    )
    if analyze and pending:
        analyze_pending(db, limit=10_000)
        pending = len(
            db.scalars(select(Answer.id).where(Answer.analysis_status == "pending")).all()
        )
    flags = recompute_flags(db)
    return {
        "interns_created": interns_created,
        "interns_updated": interns_updated,
        "answers_imported": imported,
        "answers_duplicates": duplicates,
        "pending_analysis": pending,
        "flags": flags,
        "errors": len(errors),
    }
