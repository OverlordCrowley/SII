"""Демонстрация ЛР 4 для недель 5–6.

Использует базу знаний и логический модуль. Запуск:
python -m pc_assistant.week56
или run.bat week56
"""

import sys

from .knowledge import load_knowledge
from .logic import LogicalEngine, evaluate, truth_table

RULE_IDS = {"R02", "R03", "R04", "R05"}
CASES = [
    ("Есть звуковые сигналы", {"power_on": True, "screen_on": False, "beeps": True}, "ram_failure", True),
    ("Звуковых сигналов нет", {"power_on": True, "screen_on": False, "beeps": False}, "display_failure", True),
    ("Перегрев с выключениями", {"high_temp": True, "fan_loud": False, "shutdowns": True}, "overheating", True),
    ("О сигналах ничего не известно", {"power_on": True, "screen_on": False}, "display_failure", None),
]


def value_text(value):
    return {True: "ИСТИНА", False: "ЛОЖЬ", None: "НЕИЗВЕСТНО"}[value]


def print_proof(proof, labels, level=0):
    source = proof.get("rule") or ("факт пользователя" if proof.get("source") == "input" else "не доказано")
    print("  " * level + f"{labels.get(proof['fact'], proof['fact'])}: {value_text(proof['value'])} [{source}]")
    for child in proof.get("children", []):
        print_proof(child, labels, level + 1)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    knowledge = load_knowledge()
    rules = [rule for rule in knowledge["rules"] if rule["id"] in RULE_IDS]
    assert len(rules) == 4, "В базе отсутствует правило демонстрации"
    engine = LogicalEngine(rules)
    labels = {name: data["label"] for name, data in knowledge["symptoms"].items()}
    labels.update({name: data["label"] for name, data in knowledge["diagnoses"].items()})
    labels["display_problem"] = "Предварительная проблема изображения"

    print("ЛР 4. Логическая модель знаний. Недели 5–6")
    print("Предметная область: диагностика компьютера")
    print("\nНЕДЕЛЯ 5. Факты, логические правила и вывод")
    print("Факты описывают наблюдаемые симптомы и имеют значения true/false.")
    print("Отсутствующее наблюдение имеет значение неизвестно.")
    print("\nБаза правил этого этапа:")
    for rule in rules:
        print(f"  {rule['id']}: {rule['if']} => {rule['then']}")

    print("\nНЕДЕЛЯ 6. AND, OR, NOT и объяснение результата")
    operations = [
        ("a AND b", {"a": True, "b": False}, False),
        ("a OR b", {"a": False, "b": True}, True),
        ("NOT a", {"a": False}, True),
        ("NOT a", {}, None),
    ]
    for expression, facts, expected in operations:
        result = evaluate(expression, facts)
        assert result is expected
        print(f"  {expression}, факты {facts}: {value_text(result)}")

    print("\nТаблица истинности a AND (b OR NOT c):")
    print("  a  b  c  результат")
    rows = truth_table("a AND (b OR NOT c)")
    assert len(rows) == 8 and sum(row["result"] for row in rows) == 3
    for row in rows:
        print(f"  {int(row['a'])}  {int(row['b'])}  {int(row['c'])}  {int(row['result'])}")

    print("\nЛогический вывод и объяснения:")
    for index, (name, facts, goal, expected) in enumerate(CASES, 1):
        print(f"\nПример {index}. {name}")
        print("Исходные факты:")
        for fact, value in facts.items():
            print(f"  {labels[fact]} = {value_text(value)}")
        proof = engine.explain(goal, facts)
        assert proof["value"] is expected, f"Неверный результат примера {index}"
        print("Цель и объяснение:")
        print_proof(proof, labels)
        if expected is None:
            print("Нужно уточнить: слышны ли сигналы BIOS?")

    print("\nПроверка завершена: 4 примера, 4 операции и таблица истинности корректны.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
