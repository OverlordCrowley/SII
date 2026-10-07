"""Единый запуск интерфейса, анализа, обучения и учебных демонстраций."""

import argparse
import json
from pathlib import Path
import sys

from .assistant import ProjectAssistant, markdown_report, proof_lines, parse_profile
from .knowledge import ROOT
from .logic import evaluate, truth_table


def demo(weekly=False):
    assistant = ProjectAssistant()
    if weekly:
        print("ЛР 4. Недели 5–6. Анализ криптопроектов\n")
        print("НЕДЕЛЯ 5: факты и правила")
        for rule in assistant.knowledge["rules"]:
            print(f"{rule['id']}: {rule['if']} => {rule['then']}")
        print("\nНЕДЕЛЯ 6: AND, OR, NOT")
        for expression, facts, expected in [("a AND b", {"a": True, "b": False}, False), ("a OR b", {"a": False, "b": True}, True), ("NOT a", {"a": False}, True), ("NOT a", {}, None)]:
            actual = evaluate(expression, facts)
            assert actual is expected
            print(expression, facts, "=>", actual)
        print("\nТаблица a AND (b OR NOT c)")
        for row in truth_table("a AND (b OR NOT c)"):
            print(row)
    expected = {"ready": "research", "risky": "blocked", "incomplete": "clarify"}
    labels = {key: item["label"] for key, item in assistant.criteria.items()}
    labels.update({rule["then"]: rule["label"] for rule in assistant.knowledge["rules"]})
    labels.update(assistant.knowledge["computed"])
    for name, status in expected.items():
        profile = json.loads((ROOT / f"examples/{name}.json").read_text(encoding="utf-8"))
        result = assistant.analyze(profile)
        assert result["status"] == status
        print("\n" + result["summary"])
        for item in result["findings"][:2] + result["strengths"][:1]:
            print("\n".join(proof_lines(item["proof"], labels)))
        for question in result["questions"][:2]:
            print("Уточнение:", question["question"])
    if weekly:
        unknown = assistant.logical.query("asset_identification_gap", {})
        assert unknown is None
        assert assistant.logical.query("asset_identification_gap", {"chain_asset_verified": False}) is True
        print("\nОтсутствующие сведения не являются отрицанием: проверено.")
    print("\nТри сценария и объяснения прошли проверку.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Криптоскоп — ИИ-помощник для анализа криптопроектов")
    sub = parser.add_subparsers(dest="command")
    server = sub.add_parser("serve", help="Интерфейс в браузере")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--no-browser", action="store_true")
    analyze = sub.add_parser("analyze", help="Анализ JSON, Markdown или текстового файла")
    analyze.add_argument("file", type=Path)
    analyze.add_argument("--output", type=Path, help="Новый файл отчёта Markdown")
    analyze.add_argument("--json", action="store_true")
    ask = sub.add_parser("ask", help="Анализ описания из командной строки")
    ask.add_argument("description")
    ask.add_argument("--name", default="", help="Название или тикер монеты")
    sub.add_parser("demo", help="Три проекта с проверкой результата")
    sub.add_parser("week56", help="Факты, AND/OR/NOT, вывод и объяснение")
    sub.add_parser("week48", help="Показ всех требований недель 4–8")
    sub.add_parser("week415", help="Показ реализации недель 4–15")
    train = sub.add_parser("train", help="Создать датасет и обучить обе модели")
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--epochs", type=int, default=100)
    from .workbench import add_commands
    add_commands(sub)
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "serve"):
            from .server import serve
            serve(getattr(args, "port", 8765), not getattr(args, "no_browser", False))
        elif args.command == "week48":
            from .week48 import demonstrate
            demonstrate()
        elif args.command == "week415":
            from .week415 import demonstrate
            demonstrate()
        elif args.command == "train":
            if not 1 <= args.epochs <= 500:
                raise ValueError("Число эпох должно быть от 1 до 500")
            from .dataset import train_all
            print("Создание датасета и обучение моделей...", flush=True)
            metrics = train_all(args.seed, args.epochs)
            print(json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False))
        elif args.command in ("frame", "network"):
            from .workbench import run
            run(args)
        elif args.command in ("demo", "week56"):
            demo(args.command == "week56")
        else:
            if args.command == "analyze":
                raw = args.file.read_text(encoding="utf-8-sig")
                profile = parse_profile(raw) if args.file.suffix.lower() == ".json" else {"name": args.file.stem, "description": raw}
            else:
                profile = {"name": args.name, "description": args.description}
            result = ProjectAssistant().analyze(profile)
            report = markdown_report(result)
            if args.command == "analyze" and args.output:
                with args.output.open("x", encoding="utf-8", newline="\n") as output:
                    output.write(report)
                print(f"Отчёт сохранён: {args.output.resolve()}")
            elif getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            else:
                print(report)
        return 0
    except (OSError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
