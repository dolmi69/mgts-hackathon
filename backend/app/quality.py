"""Offline and opt-in live evaluation on annotated synthetic comments."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from .config import BASE_DIR
from .models import AIAnalysis, Answer, Intern
from .prompts import PROMPT_VERSION
from .services.llm import BaseProvider, GigaChatProvider, LLMError, MockProvider
from .services.rules import evaluate_intern

DEFAULT_FIXTURE = BASE_DIR / "data" / "quality" / "feedback.csv"
BENCHMARK_PERIOD = date(2026, 3, 1)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def evaluate(fixture: Path, provider: BaseProvider, cases: set[str] | None = None) -> dict:
    with fixture.open(encoding="utf-8", newline="") as source:
        records = list(csv.DictReader(source, delimiter=";"))
    if cases is not None:
        missing = cases - {row["id"] for row in records}
        if missing:
            raise ValueError(f"Unknown case IDs: {', '.join(sorted(missing))}")
        records = [row for row in records if row["id"] in cases]

    counts: Counter[str] = Counter()
    models: set[str] = set()
    differences: list[dict] = []
    for row in records:
        score = int(row["score"])
        expected_topics = set(row["problem_topics"].split("|")) - {""}
        expected_flag = bool(expected_topics and score >= 4)
        try:
            output = provider.analyze(row["comment"], row["role"], month_index=2)
        except LLMError as exc:
            counts["errors"] += 1
            differences.append({"id": row["id"], "error": str(exc)})
            continue
        result = output.result
        models.add(output.model)
        intern = Intern(id=1, external_id="quality-example", name="Демо", start_period=BENCHMARK_PERIOD)
        answer = Answer(
            id=1,
            period=BENCHMARK_PERIOD,
            respondent_type=row["role"],
            overall_score=score,
            comment=row["comment"],
            analysis=AIAnalysis(
                status="done",
                sentiment=result.sentiment,
                problem_topics=result.problem_topics,
                evidence=result.evidence,
            ),
        )
        flags = evaluate_intern(intern, [answer], BENCHMARK_PERIOD)
        has_flag = any(flag["rule_code"] == "R1" for flag in flags)
        if has_flag:
            counts["true_positive" if expected_flag else "false_positive"] += 1
        else:
            counts["false_negative" if expected_flag else "true_negative"] += 1
        topics_match = expected_topics == set(result.problem_topics)
        tone_matches = row["sentiment"] == result.sentiment
        counts["completed"] += 1
        counts["exact_topic_matches"] += topics_match
        counts["sentiment_matches"] += tone_matches
        if not topics_match or not tone_matches or expected_flag != has_flag:
            differences.append(
                {
                    "id": row["id"],
                    "expected_r1": expected_flag,
                    "actual_r1": has_flag,
                    "expected_topics": sorted(expected_topics),
                    "actual_topics": result.problem_topics,
                    "expected_sentiment": row["sentiment"],
                    "actual_sentiment": result.sentiment,
                }
            )

    return {
        "selected": len(records),
        "completed": counts["completed"],
        "errors": counts["errors"],
        "models": sorted(models),
        "prompt_version": PROMPT_VERSION,
        "r1": {
            "true_positive": counts["true_positive"],
            "true_negative": counts["true_negative"],
            "false_positive": counts["false_positive"],
            "false_negative": counts["false_negative"],
            "precision": _ratio(counts["true_positive"], counts["true_positive"] + counts["false_positive"]),
            "recall": _ratio(counts["true_positive"], counts["true_positive"] + counts["false_negative"]),
        },
        "exact_problem_topics_rate": _ratio(counts["exact_topic_matches"], counts["completed"]),
        "sentiment_accuracy": _ratio(counts["sentiment_matches"], counts["completed"]),
        "differences": differences,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate R1 and complaint topics on synthetic comments")
    parser.add_argument("--provider", choices=["mock", "gigachat"], default="mock")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--case", action="append", help="Run one case ID. Repeat to select multiple")
    args = parser.parse_args()
    try:
        provider = GigaChatProvider() if args.provider == "gigachat" else MockProvider()
        report = evaluate(args.fixture, provider, set(args.case) if args.case else None)
    except (OSError, ValueError, LLMError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
