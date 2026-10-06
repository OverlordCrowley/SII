"""Логика высказываний с явным неизвестным значением.

NOT неизвестного остаётся неизвестным. Отсутствие сведений в описании
не является доказательством ложности критерия.
"""

import re


def parse(expression):
    """Безопасный разбор AND, OR, NOT и скобок без eval()."""
    tokens, position = [], 0
    while position < len(expression):
        if not expression[position:].strip():
            break
        match = re.match(r"\s*([A-Za-z_][A-Za-z_0-9]*|[()])", expression[position:])
        if not match:
            raise ValueError(f"Недопустимый символ в позиции {position}")
        token = match.group(1)
        tokens.append(token.upper() if token.upper() in {"AND", "OR", "NOT"} else token.lower())
        position += match.end()
    if not tokens or len(tokens) > 256:
        raise ValueError("Выражение пустое или слишком длинное")
    index = 0

    def unary():
        nonlocal index
        if index >= len(tokens):
            raise ValueError("Ожидался операнд")
        token = tokens[index]
        index += 1
        if token == "NOT":
            return ("NOT", unary())
        if token == "(":
            node = disjunction()
            if index >= len(tokens) or tokens[index] != ")":
                raise ValueError("Не закрыта скобка")
            index += 1
            return node
        if token in {"AND", "OR", ")"}:
            raise ValueError(f"Неожиданный токен {token}")
        return ("ATOM", token)

    def conjunction():
        nonlocal index
        node = unary()
        while index < len(tokens) and tokens[index] == "AND":
            index += 1
            node = ("AND", node, unary())
        return node

    def disjunction():
        nonlocal index
        node = conjunction()
        while index < len(tokens) and tokens[index] == "OR":
            index += 1
            node = ("OR", node, conjunction())
        return node

    result = disjunction()
    if index != len(tokens):
        raise ValueError("Лишние токены в выражении")
    return result


def atoms(node):
    if node[0] == "ATOM":
        return {node[1]}
    return set().union(*(atoms(child) for child in node[1:]))


def negative_atoms(node):
    if node[0] == "NOT":
        return atoms(node[1])
    if node[0] == "ATOM":
        return set()
    return set().union(*(negative_atoms(child) for child in node[1:]))


def evaluate_node(node, lookup):
    op = node[0]
    if op == "ATOM":
        return lookup(node[1])
    if op == "NOT":
        value = evaluate_node(node[1], lookup)
        return None if value is None else not value
    values = [evaluate_node(child, lookup) for child in node[1:]]
    if op == "AND":
        return False if False in values else (True if all(v is True for v in values) else None)
    return True if True in values else (False if all(v is False for v in values) else None)


def validate_facts(facts):
    if any(type(value) is not bool for value in facts.values()):
        raise ValueError("Значения фактов должны быть true или false")


def evaluate(expression, facts):
    validate_facts(facts)
    return evaluate_node(parse(expression), facts.get)


def support_atoms(node, facts, expected=True):
    """Минимальная подтверждающая ветвь для истинного или ложного условия."""
    if node[0] == "ATOM":
        return [node[1]]
    if node[0] == "NOT":
        return support_atoms(node[1], facts, not expected)
    if (node[0] == "OR" and expected) or (node[0] == "AND" and not expected):
        for child in node[1:]:
            if evaluate_node(child, facts.get) is expected:
                return support_atoms(child, facts, expected)
    return list(dict.fromkeys(name for child in node[1:]
                              for name in support_atoms(child, facts, expected)))


class LogicalEngine:
    """Обратный вывод: проверяет заданную цель по фактам и правилам."""

    def __init__(self, rules):
        self.rules = [(rule, parse(rule["if"])) for rule in rules]
        conclusions = {rule["then"] for rule in rules}
        if any(negative_atoms(node) & conclusions for _, node in self.rules):
            raise ValueError("NOT допускается только для исходных фактов")

    def query(self, goal, facts):
        return self.explain(goal, facts)["value"]

    def explain(self, goal, facts):
        """Возвращает дерево доказательства, включая явные отрицания."""
        validate_facts(facts)

        def solve(atom, visiting):
            if atom in facts:
                return {"fact": atom, "value": facts[atom], "source": "input"}
            if atom in visiting:
                return {"fact": atom, "value": None, "source": "cycle"}
            next_path = visiting | {atom}
            for rule, node in self.rules:
                if rule["then"] != atom:
                    continue
                proofs = {name: solve(name, next_path) for name in atoms(node)}
                values = {name: proof["value"] for name, proof in proofs.items()}
                if evaluate_node(node, values.get) is True:
                    return {"fact": atom, "value": True, "rule": rule["id"],
                            "condition": rule["if"],
                            "children": [proofs[name] for name in support_atoms(node, values)]}
            return {"fact": atom, "value": None, "source": "not_proven"}

        return solve(goal, set())


def truth_table(expression):
    """Классическая таблица истинности для полностью заданных переменных."""
    from itertools import product

    node = parse(expression)
    names = sorted(atoms(node))
    if len(names) > 10:
        raise ValueError("Таблица ограничена 10 переменными")
    rows = []
    for values in product((False, True), repeat=len(names)):
        facts = dict(zip(names, values))
        rows.append({**facts, "result": evaluate_node(node, facts.get)})
    return rows
