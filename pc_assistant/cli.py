"""Консольный интерфейс всех лабораторных модулей."""

import argparse
import json
import sys

from .assistant import Assistant, format_answer
from .expert import ExpertSystem
from .knowledge import ROOT
from .logic import truth_table

DEMO_CASES = [
    ("Питание", "Компьютер не включается, вентиляторы не вращаются"),
    ("Охлаждение", "Компьютер перегревается, вентилятор шумит, сам выключается"),
    ("Накопитель", "Компьютер тормозит, диск щелкает, ошибки диска"),
    ("Память", "Компьютер включается, черный экран, пищит"),
    ("Сеть", "Нет интернета, wifi подключен, компьютер включается"),
    ("Программы", "Тормозит, много приложений, нет ошибок диска"),
    ("Неизвестный симптом", "Компьютер включается, черный экран")
]


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def interactive(expert):
    assistant = Assistant(expert)
    print("Опишите симптомы. Для выхода введите выход.")
    while True:
        try:
            query = input("\nЗапрос: ").strip()
        except EOFError:
            return
        if query.lower() in {"выход", "exit", "quit"}:
            return
        if not query:
            continue
        try:
            result = assistant.ask(query)
            print(result["answer"])
            facts = dict(result["input_facts"])
            # Один проход уточнений; неизвестный ответ не добавляется как false.
            for question in result["questions"]:
                reply = input(question["label"] + "? [да/нет/?/готово]: ").strip().lower()
                if reply in {"готово", "выход", "exit"}:
                    break
                if reply in {"да", "yes", "y", "1"}:
                    facts[question["id"]] = True
                elif reply in {"нет", "no", "n", "0"}:
                    facts[question["id"]] = False
            if facts != result["input_facts"]:
                print(format_answer(expert.diagnose(facts), expert.knowledge))
        except (ValueError, EOFError) as error:
            print("Ошибка: " + str(error))


def build_parser():
    parser = argparse.ArgumentParser(description="Учебный ИИ-помощник по диагностике ПК")
    parser.add_argument("--no-models", action="store_true", help="Работать без NumPy и обученных моделей")
    commands = parser.add_subparsers(dest="command")
    ask = commands.add_parser("ask", help="Проанализировать запрос")
    ask.add_argument("query", nargs="?", default=None)
    ask.add_argument("--facts", help='JSON со значениями true/false')
    ask.add_argument("--device", default="office_pc")
    ask.add_argument("--json", action="store_true")
    commands.add_parser("demo", help="Семь сценариев для защиты")
    commands.add_parser("chat", help="Диалог с уточняющими вопросами")
    commands.add_parser("symptoms", help="Словарь симптомов и фраз")
    commands.add_parser("rules", help="База продукционных правил")
    train = commands.add_parser("train", help="Обучить оба классификатора")
    train.add_argument("--generate-dataset", action="store_true")
    logic = commands.add_parser("logic", help="Таблица истинности или обратный вывод")
    logic.add_argument("goal")
    logic.add_argument("--facts", default="{}")
    logic.add_argument("--table", action="store_true")
    frames = commands.add_parser("frames", help="Просмотр, поиск и изменение фреймов")
    operations = frames.add_subparsers(dest="operation", required=True)
    operations.add_parser("list")
    show = operations.add_parser("show")
    show.add_argument("name")
    find = operations.add_parser("find")
    find.add_argument("slot")
    find.add_argument("value", help='Значение JSON, например 16 или true или "Windows"')
    change = operations.add_parser("set")
    change.add_argument("name")
    change.add_argument("slot")
    change.add_argument("value", help="Значение JSON")
    create = operations.add_parser("create")
    create.add_argument("name")
    create.add_argument("parent")
    create.add_argument("--slots", default="{}")
    network = commands.add_parser("network", help="Поиск и изменение семантической сети")
    operations = network.add_subparsers(dest="operation", required=True)
    operations.add_parser("list")
    path = operations.add_parser("path")
    path.add_argument("source")
    path.add_argument("target")
    related = operations.add_parser("related")
    related.add_argument("source")
    related.add_argument("--depth", type=int, default=1)
    for operation in (path, related):
        operation.add_argument("--relation")
        operation.add_argument("--undirected", action="store_true")
    node = operations.add_parser("add-node")
    node.add_argument("name")
    node.add_argument("label")
    edge = operations.add_parser("add-edge")
    edge.add_argument("source")
    edge.add_argument("relation")
    edge.add_argument("target")
    return parser


def json_object(text):
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("Ожидается объект JSON")
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            from .dataset import generate
            from .ml import train_bayes, train_neural
            if args.generate_dataset or not (ROOT / "data" / "dataset.csv").exists():
                generate()
            train_bayes()
            result = train_neural()
            print_json(result)
            return 0
        expert = ExpertSystem(use_models=not args.no_models)
        if args.command in {None, "chat"}:
            interactive(expert)
        elif args.command == "ask":
            facts = json_object(args.facts) if args.facts else {}
            if args.query:
                result = Assistant(expert).ask(args.query, args.device, facts)
            elif args.facts:
                result = expert.diagnose(facts, args.device)
                result["answer"] = format_answer(result, expert.knowledge)
            else:
                raise ValueError("Укажите текст запроса или --facts")
            print_json(result) if args.json else print(result["answer"])
        elif args.command == "demo":
            assistant = Assistant(expert)
            for name, query in DEMO_CASES:
                print(f"\n{'=' * 60}\n{name}\nЗапрос: {query}\n")
                print(assistant.ask(query)["answer"])
        elif args.command in {"symptoms", "rules"}:
            print_json(expert.knowledge[args.command])
        elif args.command == "logic":
            print_json(truth_table(args.goal) if args.table else expert.logical.explain(args.goal, json_object(args.facts)))
        elif args.command == "frames":
            if args.operation == "list":
                print_json(sorted(expert.frames.frames))
            elif args.operation == "show":
                print_json(expert.frames.describe(args.name))
            elif args.operation == "find":
                print_json(expert.frames.find(args.slot, json.loads(args.value)))
            elif args.operation == "set":
                result = expert.frames.set(args.name, args.slot, json.loads(args.value))
                expert.save_frames()
                print_json(result)
            elif args.operation == "create":
                result = expert.frames.create(args.name, args.parent, json_object(args.slots))
                expert.save_frames()
                print_json(result)
        elif args.command == "network":
            if args.operation == "list":
                print_json({"nodes": expert.network.nodes, "edges": expert.network.edges})
            elif args.operation == "path":
                print_json(expert.network.path(args.source, args.target, args.relation, args.undirected))
            elif args.operation == "related":
                print_json(expert.network.related(args.source, args.depth, args.relation, args.undirected))
            elif args.operation == "add-node":
                expert.network.add_node(args.name, args.label)
                expert.save_network()
                print("Узел сохранён")
            elif args.operation == "add-edge":
                expert.network.add_edge(args.source, args.relation, args.target)
                expert.save_network()
                print("Связь сохранена")
        return 0
    except (ValueError, KeyError, OSError, TypeError, ImportError) as error:
        print("Ошибка: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
