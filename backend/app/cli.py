from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import delete, func, select

from .database import SessionLocal, init_db, reset_db
from .models import Answer
from .seed_data import generate_seed_files, seed_database
from .services.analysis import analyze_pending
from .services.rules import recompute_flags


def _print_stats(stats: dict) -> None:
    for key, value in stats.items():
        print(f"{key}: {value}")


def cmd_init(args: argparse.Namespace) -> None:
    if args.reset:
        reset_db()
    init_db()
    if args.seed:
        with SessionLocal() as db:
            stats = seed_database(db, analyze=not args.no_analyze, reset=args.reset)
            _print_stats(stats)
    print("База готова.")


def cmd_seed(args: argparse.Namespace) -> None:
    init_db()
    with SessionLocal() as db:
        stats = seed_database(db, analyze=not args.no_analyze, reset=args.reset)
        _print_stats(stats)


def cmd_analyze(args: argparse.Namespace) -> None:
    init_db()
    with SessionLocal() as db:
        count = analyze_pending(db, limit=args.limit)
        print(f"Проанализировано ответов: {count}")
        print(f"Флажков после пересчёта: {recompute_flags(db)}")


def cmd_recompute(_: argparse.Namespace) -> None:
    init_db()
    with SessionLocal() as db:
        print(f"Флажков: {recompute_flags(db)}")


def cmd_reset(args: argparse.Namespace) -> None:
    reset_db()
    print("База очищена.")
    if args.seed:
        cmd_seed(args)


def cmd_export(args: argparse.Namespace) -> None:
    target = generate_seed_files(force=True)
    print(f"CSV записаны в {target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="МГТС · демо-данные и обслуживание")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Создать таблицы и, при желании, демо-данные")
    init_parser.add_argument("--seed", action="store_true", help="Загрузить демо-данные")
    init_parser.add_argument("--reset", action="store_true", help="Сначала очистить базу")
    init_parser.add_argument("--no-analyze", action="store_true", help="Не вызывать LLM")
    init_parser.set_defaults(func=cmd_init)

    seed_parser = sub.add_parser("seed", help="Загрузить демо-данные")
    seed_parser.add_argument("--reset", action="store_true", help="Очистить перед загрузкой")
    seed_parser.add_argument("--no-analyze", action="store_true", help="Только данные, без LLM")
    seed_parser.set_defaults(func=cmd_seed)

    analyze_parser = sub.add_parser("analyze", help="Разобрать ожидающие ответы")
    analyze_parser.add_argument("--limit", type=int, default=10_000)
    analyze_parser.set_defaults(func=cmd_analyze)

    recompute_parser = sub.add_parser("recompute", help="Пересчитать флажки")
    recompute_parser.set_defaults(func=cmd_recompute)

    reset_parser = sub.add_parser("reset", help="Удалить все данные")
    reset_parser.add_argument("--seed", action="store_true", help="Сразу загрузить демо-данные")
    reset_parser.add_argument("--no-analyze", action="store_true")
    reset_parser.set_defaults(func=cmd_reset)

    export_parser = sub.add_parser("export", help="Перегенерировать демо-CSV")
    export_parser.set_defaults(func=cmd_export)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
