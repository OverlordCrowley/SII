"""Наивный байесовский классификатор Бернулли и метрики."""

from collections import Counter
import numpy as np

from .dataset import read_dataset
from .knowledge import ROOT, save_json


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
