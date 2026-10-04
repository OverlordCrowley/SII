"""Наивный байесовский классификатор Бернулли и метрики."""

from collections import Counter
import json
import numpy as np

from .dataset import read_dataset
from .knowledge import ROOT, save_json
from .logic import validate_facts


def arrays(rows, features, labels, split):
    selected = [row for row in rows if row["split"] == split]
    return (np.array([[int(row[f]) for f in features] for row in selected], dtype=float),
            np.array([labels.index(row["label"]) for row in selected], dtype=int))


def softmax(scores):
    scores = scores - np.max(scores, axis=-1, keepdims=True)
    values = np.exp(scores)
    return values / values.sum(axis=-1, keepdims=True)


def metrics(actual, predicted, labels):
    matrix = np.zeros((len(labels), len(labels)), dtype=int)
    for truth, guess in zip(actual, predicted):
        matrix[truth, guess] += 1
    per_class = {}
    for i, label in enumerate(labels):
        tp = int(matrix[i, i])
        precision = tp / max(1, int(matrix[:, i].sum()))
        recall = tp / max(1, int(matrix[i].sum()))
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1,
                            "support": int(matrix[i].sum())}
    return {"accuracy": float(np.mean(actual == predicted)),
            "macro_f1": float(np.mean([m["f1"] for m in per_class.values()])),
            "confusion_matrix": matrix.tolist(), "labels": labels, "per_class": per_class}


class BernoulliNB:
    def fit(self, x, y, classes):
        counts = np.bincount(y, minlength=classes)
        if np.any(counts == 0):
            raise ValueError("В обучающей выборке отсутствует класс")
        self.prior = counts / len(y)
        self.probability = np.array([(x[y == i].sum(axis=0) + 1) / (counts[i] + 2)
                                     for i in range(classes)])
        return self

    def predict_proba(self, x, observed=None):
        x = np.atleast_2d(x)
        mask = np.ones_like(x) if observed is None else np.atleast_2d(observed)
        p = self.probability
        scores = np.log(self.prior) + (x * mask) @ np.log(p).T + ((1 - x) * mask) @ np.log(1 - p).T
        return softmax(scores)

    def serialize(self):
        return {"prior": self.prior.tolist(), "probability": self.probability.tolist()}

    @classmethod
    def restore(cls, data):
        model = cls()
        model.prior = np.array(data["prior"])
        model.probability = np.array(data["probability"])
        return model


def train_bayes():
    rows, metadata = read_dataset()
    features, labels = metadata["features"], metadata["labels"]
    x_train, y_train = arrays(rows, features, labels, "train")
    x_test, y_test = arrays(rows, features, labels, "test")
    model = BernoulliNB().fit(x_train, y_train, len(labels))
    report = {"dataset_sha256": metadata["sha256"], "source": "synthetic",
              "split_counts": dict(Counter(row["split"] for row in rows)),
              "majority_baseline_accuracy": float(np.mean(y_test == np.bincount(y_train).argmax())),
              "bayes": metrics(y_test, model.predict_proba(x_test).argmax(axis=1), labels)}
    save_json(ROOT / "models" / "bayes.json", {"version": 1, "features": features, "labels": labels,
              "dataset_sha256": metadata["sha256"], "model": model.serialize()})
    save_json(ROOT / "models" / "metrics.json", report)
    return report


class NeuralNetwork:
    """MLP: вход, скрытый слой tanh, выход softmax. Градиенты вручную."""

    def fit(self, x, y, x_validation, y_validation, classes, seed=42,
            hidden=16, epochs=600, learning_rate=.18):
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0, 1 / np.sqrt(x.shape[1]), (x.shape[1], hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0, 1 / np.sqrt(hidden), (hidden, classes))
        self.b2 = np.zeros(classes)
        expected = np.eye(classes)[y]
        best_loss, best_weights, best_epoch = float("inf"), None, 0
        history = []
        for epoch in range(1, epochs + 1):
            # Моделируем неизвестные симптомы; test не участвует в обучении.
            masked = np.where(rng.random(x.shape) < .1, .5, x) * 2 - 1
            hidden_values = np.tanh(masked @ self.w1 + self.b1)
            probabilities = softmax(hidden_values @ self.w2 + self.b2)
            output_gradient = (probabilities - expected) / len(y)
            hidden_gradient = (output_gradient @ self.w2.T) * (1 - hidden_values ** 2)
            gradient_w2 = hidden_values.T @ output_gradient + 1e-4 * self.w2
            gradient_w1 = masked.T @ hidden_gradient + 1e-4 * self.w1
            self.w2 -= learning_rate * gradient_w2
            self.b2 -= learning_rate * output_gradient.sum(axis=0)
            self.w1 -= learning_rate * gradient_w1
            self.b1 -= learning_rate * hidden_gradient.sum(axis=0)
            validation_p = self.predict_proba(x_validation)
            validation_loss = float(-np.log(np.maximum(validation_p[np.arange(len(y_validation)), y_validation], 1e-12)).mean())
            if validation_loss < best_loss:
                best_loss, best_epoch = validation_loss, epoch
                best_weights = [weight.copy() for weight in (self.w1, self.b1, self.w2, self.b2)]
            if epoch == 1 or epoch % 50 == 0:
                train_loss = float(-np.log(np.maximum(probabilities[np.arange(len(y)), y], 1e-12)).mean())
                history.append({"epoch": epoch, "train_loss_with_masking": train_loss,
                                "validation_loss": validation_loss})
        self.w1, self.b1, self.w2, self.b2 = best_weights
        self.training = {"seed": seed, "hidden": hidden, "epochs": epochs, "learning_rate": learning_rate,
                         "selected_epoch": best_epoch, "selection": "minimum validation cross entropy",
                         "history": history}
        return self

    def predict_proba(self, x):
        x = np.atleast_2d(x) * 2 - 1
        return softmax(np.tanh(x @ self.w1 + self.b1) @ self.w2 + self.b2)

    def serialize(self):
        return {name: getattr(self, name).tolist() for name in ("w1", "b1", "w2", "b2")}

    @classmethod
    def restore(cls, data):
        model = cls()
        for name in ("w1", "b1", "w2", "b2"):
            setattr(model, name, np.array(data[name], dtype=float))
        return model


def train_neural():
    rows, metadata = read_dataset()
    features, labels = metadata["features"], metadata["labels"]
    x_train, y_train = arrays(rows, features, labels, "train")
    x_validation, y_validation = arrays(rows, features, labels, "validation")
    x_test, y_test = arrays(rows, features, labels, "test")
    model = NeuralNetwork().fit(x_train, y_train, x_validation, y_validation, len(labels))
    path = ROOT / "models" / "metrics.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if report.get("dataset_sha256") != metadata["sha256"]:
        report = train_bayes()
    report["neural"] = metrics(y_test, model.predict_proba(x_test).argmax(axis=1), labels)
    report["neural_training"] = model.training
    save_json(ROOT / "models" / "neural.json", {"version": 1, "features": features, "labels": labels,
              "dataset_sha256": metadata["sha256"], "architecture": [len(features), 16, len(labels)],
              "model": model.serialize()})
    save_json(path, report)
    return report


def rank_models(facts, knowledge, model_dir=None):
    validate_facts(facts)
    features, labels = list(knowledge["symptoms"]), list(knowledge["diagnoses"])
    observed = np.array([feature in facts for feature in features], dtype=float)
    # ponytail: при менее чем 3 наблюдениях рейтинг мало полезен; полноценный
    # контроль неопределённости потребует отдельного реального датасета.
    if observed.sum() < 3:
        return {"available": False, "reason": "Для рейтинга моделей нужны минимум 3 известных симптома"}
    x = np.array([float(facts[f]) if f in facts else .5 for f in features])
    model_dir = model_dir or ROOT / "models"
    output = {"available": True, "source": "synthetic", "observed": int(observed.sum()),
              "note": "Оценки моделей на синтетических данных не являются вероятностью реальной неисправности."}
    fingerprint = None
    for name, model_class in (("bayes", BernoulliNB), ("neural", NeuralNetwork)):
        artifact = json.loads((model_dir / f"{name}.json").read_text(encoding="utf-8"))
        if artifact.get("version") != 1 or artifact["features"] != features or artifact["labels"] != labels:
            raise ValueError("Модель не соответствует текущей базе знаний. Выполните train")
        if fingerprint is not None and fingerprint != artifact["dataset_sha256"]:
            raise ValueError("Модели обучены на разных версиях датасета. Выполните train")
        fingerprint = artifact["dataset_sha256"]
        model = model_class.restore(artifact["model"])
        scores = model.predict_proba(x, observed)[0] if name == "bayes" else model.predict_proba(x)[0]
        if not np.isfinite(scores).all() or abs(float(scores.sum()) - 1) > 1e-6:
            raise ValueError("Повреждённая модель")
        order = np.argsort(-scores, kind="stable")[:3]
        output[name] = [{"id": labels[i], "label": knowledge["diagnoses"][labels[i]]["label"],
                         "score": float(scores[i])} for i in order]
    return output
