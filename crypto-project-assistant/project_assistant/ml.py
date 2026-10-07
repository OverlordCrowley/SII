"""Учебные Bernoulli Naive Bayes и MLP: обучение и распознавание без пакетов."""

from copy import deepcopy
import math
import random

from .knowledge import ROOT

CLASSES = ["research", "revise", "blocked", "clarify"]
CLASS_LABELS = {"research": "Основа для сравнения", "revise": "Нужна доработка",
                "blocked": "Критичные сведения", "clarify": "Нужны уточнения"}
SCOPE = "Синтетические примеры с метками экспертных правил. Точность показывает воспроизведение этих правил, а не качество реальных монет. Решение и объяснение задаёт экспертная система."


def fact_names(knowledge):
    return list(knowledge["criteria"]) + list(knowledge["computed"])


def encode(facts, names):
    # Два признака на факт: известность и положительное значение.
    # Неизвестно (0,0), нет (1,0), да (1,1).
    return [bit for name in names for bit in
            (int(facts.get(name) is not None), int(facts.get(name) is True))]


def softmax(values):
    peak = max(values)
    exps = [math.exp(value - peak) for value in values]
    total = sum(exps)
    return [value / total for value in exps]


def train_bayes(rows, width):
    counts = [0] * len(CLASSES)
    ones = [[0] * width for _ in CLASSES]
    for features, label in rows:
        c = CLASSES.index(label)
        counts[c] += 1
        for j, value in enumerate(features):
            ones[c][j] += value
    return {"kind": "bernoulli_nb", "priors": [(n + 1) / (len(rows) + len(CLASSES)) for n in counts],
            "probabilities": [[(n + 1) / (counts[c] + 2) for n in values] for c, values in enumerate(ones)]}


def forward_neural(model, features):
    hidden = [math.tanh(bias + sum(w * x for w, x in zip(weights, features)))
              for weights, bias in zip(model["w1"], model["b1"])]
    probabilities = softmax([bias + sum(w * h for w, h in zip(weights, hidden))
                             for weights, bias in zip(model["w2"], model["b2"])])
    return hidden, probabilities


def probabilities(model, features):
    if model["kind"] == "bernoulli_nb":
        return softmax([math.log(prior) + sum(math.log(p if value else 1 - p)
                        for value, p in zip(features, ps))
                        for prior, ps in zip(model["priors"], model["probabilities"])])
    return forward_neural(model, features)[1]


def predict(model, features):
    ps = probabilities(model, features)
    return CLASSES[max(range(len(ps)), key=ps.__getitem__)]


def evaluate(model, rows):
    matrix = [[0] * len(CLASSES) for _ in CLASSES]
    for features, label in rows:
        matrix[CLASSES.index(label)][CLASSES.index(predict(model, features))] += 1
    per_class = {}
    for c, name in enumerate(CLASSES):
        tp = matrix[c][c]
        support = sum(matrix[c])
        predicted = sum(row[c] for row in matrix)
        precision = tp / predicted if predicted else 0
        recall = tp / support if support else 0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
        per_class[name] = {"precision": precision, "recall": recall, "f1": f1, "support": support}
    return {"accuracy": sum(matrix[c][c] for c in range(len(CLASSES))) / len(rows),
            "macro_f1": math.fsum(item["f1"] for item in per_class.values()) / len(CLASSES),
            "classes": CLASSES, "confusion_matrix": matrix, "per_class": per_class, "count": len(rows)}


def train_neural(train, validation, width, seed=42, epochs=100, hidden_size=16):
    rng = random.Random(seed)
    model = {"kind": "mlp", "w1": [[rng.uniform(-1, 1) / math.sqrt(width) for _ in range(width)] for _ in range(hidden_size)],
             "b1": [0.0] * hidden_size,
             "w2": [[rng.uniform(-1, 1) / math.sqrt(hidden_size) for _ in range(hidden_size)] for _ in CLASSES],
             "b2": [0.0] * len(CLASSES)}
    best, best_loss, best_epoch = None, float("inf"), 0
    ordered = list(train)
    for epoch in range(1, epochs + 1):
        rng.shuffle(ordered)
        rate = 0.025 / (1 + epoch / 60)
        for features, label in ordered:
            hidden, ps = forward_neural(model, features)
            out = [p - int(c == CLASSES.index(label)) for c, p in enumerate(ps)]
            delta = [(1 - h * h) * sum(model["w2"][c][j] * out[c] for c in range(len(CLASSES)))
                     for j, h in enumerate(hidden)]
            for c in range(len(CLASSES)):
                model["b2"][c] -= rate * out[c]
                for j, h in enumerate(hidden):
                    model["w2"][c][j] -= rate * (out[c] * h + 0.0001 * model["w2"][c][j])
            for j in range(hidden_size):
                model["b1"][j] -= rate * delta[j]
                for k, x in enumerate(features):
                    model["w1"][j][k] -= rate * (delta[j] * x + 0.0001 * model["w1"][j][k])
        loss = -sum(math.log(max(1e-15, probabilities(model, x)[CLASSES.index(y)])) for x, y in validation) / len(validation)
        if loss < best_loss:
            best, best_loss, best_epoch = deepcopy(model), loss, epoch
    best["training"] = {"seed": seed, "epochs": epochs, "selected_epoch": best_epoch,
                        "hidden_size": hidden_size, "validation_loss": best_loss,
                        "selection": "minimum validation cross-entropy", "optimizer": "SGD", "activation": "tanh/softmax"}
    return best


class ModelStore:
    def __init__(self, knowledge):
        from .dataset import knowledge_digest
        self.names = fact_names(knowledge)
        self.models, self.metrics = {}, {}
        self.reason = "Модели не обучены. Выполните python -m project_assistant train."
        directory = ROOT / "models"
        if not (directory / "metrics.json").exists():
            return
        import json
        try:
            metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
            if metrics["knowledge_sha256"] != knowledge_digest(knowledge):
                raise ValueError("Метрики не соответствуют базе знаний")
            for key in ("bayes", "neural"):
                model = json.loads((directory / (key + ".json")).read_text(encoding="utf-8"))
                if (model["version"] != 1 or model["fact_names"] != self.names or model["classes"] != CLASSES
                        or model["knowledge_sha256"] != knowledge_digest(knowledge)
                        or model["dataset_sha256"] != metrics["dataset_sha256"]):
                    raise ValueError("Модель не соответствует базе знаний или датасету")
                width = len(self.names) * 2
                if key == "bayes":
                    if model["kind"] != "bernoulli_nb" or len(model["priors"]) != len(CLASSES) or len(model["probabilities"]) != len(CLASSES) or any(len(row) != width for row in model["probabilities"]):
                        raise ValueError("Некорректные размеры модели Байеса")
                    parameters = [*model["priors"], *(p for row in model["probabilities"] for p in row)]
                    if not all(type(p) in (int, float) and 0 < p < 1 for p in parameters):
                        raise ValueError("Некорректные вероятности модели Байеса")
                else:
                    hidden = model["training"]["hidden_size"]
                    if model["kind"] != "mlp" or len(model["w1"]) != hidden or len(model["b1"]) != hidden or any(len(row) != width for row in model["w1"]) or len(model["w2"]) != len(CLASSES) or len(model["b2"]) != len(CLASSES) or any(len(row) != hidden for row in model["w2"]):
                        raise ValueError("Некорректные размеры нейросети")
                    parameters = [*model["b1"], *model["b2"], *(p for row in model["w1"] + model["w2"] for p in row)]
                    if not all(type(p) in (int, float) and math.isfinite(p) for p in parameters):
                        raise ValueError("Некорректные веса нейросети")
                ps = probabilities(model, [0] * (len(self.names) * 2))
                if len(ps) != len(CLASSES) or not all(math.isfinite(p) for p in ps):
                    raise ValueError("Некорректные веса модели")
                self.models[key] = model
            self.metrics = metrics
            self.reason = ""
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            self.models = {}
            self.reason = "Модели повреждены или устарели. Повторите команду train."

    def recognize(self, facts, expert_status):
        predictions = []
        features = encode(facts, self.names)
        for key, model in self.models.items():
            ps = probabilities(model, features)
            status = CLASSES[max(range(len(ps)), key=ps.__getitem__)]
            predictions.append({"model": key, "status": status, "label": CLASS_LABELS[status],
                                "probabilities": dict(zip(CLASSES, ps)), "agrees_with_expert": status == expert_status,
                                "test_accuracy": self.metrics[key]["test"]["accuracy"],
                                "test_macro_f1": self.metrics[key]["test"]["macro_f1"]})
        return {"available": bool(predictions), "predictions": predictions, "scope": SCOPE, "reason": self.reason}
