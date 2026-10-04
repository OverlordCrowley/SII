"""Прямой продукционный вывод до неподвижной точки."""

from .logic import LogicalEngine, evaluate_node, validate_facts


def support(node, facts):
    """Только факты, которые подтверждают сработавшую ветвь условия."""
    if node[0] == "ATOM":
        return [node[1]]
    if node[0] == "OR":
        for child in node[1:]:
            if evaluate_node(child, facts.get) is True:
                return support(child, facts)
    return list(dict.fromkeys(name for child in node[1:] for name in support(child, facts)))


class ProductionEngine:
    def __init__(self, rules):
        # NOT над выводимым фактом нарушил бы монотонность вывода.
        self.rules = sorted(LogicalEngine(rules).rules,
                            key=lambda pair: (-pair[0].get("priority", 0), pair[0]["id"]))

    def run(self, initial):
        validate_facts(initial)
        facts = dict(initial)
        trace = []
        while True:
            changed = False
            for rule, node in self.rules:
                if evaluate_node(node, facts.get) is not True:
                    continue
                conclusion = rule["then"]
                if facts.get(conclusion) is False:
                    raise ValueError(f"Правило {rule['id']} противоречит факту {conclusion}=false")
                if conclusion in facts:
                    continue
                reasons = [{"fact": name, "value": facts.get(name)} for name in support(node, facts)]
                facts[conclusion] = True
                trace.append({"rule": rule["id"], "condition": rule["if"],
                              "conclusion": conclusion, "premises": reasons, "step": len(trace) + 1})
                changed = True
            if not changed:
                return {"facts": facts, "trace": trace, "initial": dict(initial)}

    @staticmethod
    def explain(goal, result):
        events = {event["conclusion"]: event for event in result["trace"]}

        def build(name):
            if name in result["initial"]:
                return {"fact": name, "value": result["initial"][name], "source": "input"}
            event = events.get(name)
            if event is None:
                return {"fact": name, "value": None, "source": "not_proven"}
            return {"fact": name, "value": True, "rule": event["rule"],
                    "condition": event["condition"],
                    "children": [build(premise["fact"]) for premise in event["premises"]]}

        return build(goal)
