"""Воспроизводимый синтетический датасет; непересекающиеся split по фактам."""

from collections import Counter
import csv
import hashlib
import json
import random

from .expert import ExpertSystem, applicable_facts, decision
from .knowledge import ROOT, save_json
from .ml import CLASSES, SCOPE, encode, evaluate, fact_names, train_bayes, train_neural


def knowledge_digest(knowledge):
    raw = json.dumps(knowledge, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def generate(knowledge, seed=42):
    rng = random.Random(seed)
    expert = ExpertSystem(state_dir=None)
    names = fact_names(knowledge)
    required = {"research": 140, "revise": 400, "blocked": 400, "clarify": 400}
    groups = {key: [] for key in CLASSES}
    seen = set()
    for attempt in range(50000):
        family = CLASSES[attempt % len(CLASSES)]
        if len(groups[family]) >= required[family]:
            continue
        facts = {}
        for name in knowledge["criteria"]:
            if family == "research":
                facts[name] = rng.choice([True, False, None]) if name in {"problem_defined", "revenue_disclosed", "competition_analyzed"} else True
            elif family == "clarify":
                facts[name] = rng.choice([True, False, None]) if name in {"problem_defined", "revenue_disclosed", "competition_analyzed", "liquidity_known", "valuation_comparable", "incident_history_known", "roadmap_delivery", "has_token"} else rng.choice([True, True, None])
            else:
                facts[name] = rng.choices([True, False, None], [6, 2, 2])[0]
        if family == "research":
            facts["has_token"] = rng.choice([True, False])
        if family == "blocked":
            cause = rng.choice([("chain_asset_verified",), ("team_identified", "code_public"), ("audit_available", "admin_controls_known")])
            for name in cause:
                facts[name] = False
        if family == "revise":
            facts["chain_asset_verified"] = True
            facts["team_identified"] = True
            facts["audit_available"] = True
            cause = rng.choice(["product_exists", "users_active", "usage_evidence", "code_public", "sources_current", "metrics_dated", "token_value_link", "vesting_disclosed", "unlock_schedule_known", "supply_disclosed", "emission_disclosed", "holder_rights_clear"])
            facts[cause] = False
            if knowledge["criteria"][cause].get("applies_if"):
                facts["has_token"] = True
        gap, low = rng.choice([(None, None), (False, False), (True, False), (True, True)])
        facts.update(fdv_gap_high=gap, low_float=low, large_unlock=rng.choice([False, None] if family == "research" else [True, False, None]))
        facts = applicable_facts(knowledge, facts)
        features = tuple(encode(facts, names))
        label = decision(knowledge, expert.production.run(facts))
        if features in seen or label != family:
            continue
        seen.add(features)
        groups[label].append(facts)
        if all(len(groups[c]) == required[c] for c in CLASSES):
            break
    if any(len(groups[c]) < required[c] for c in CLASSES):
        raise ValueError("Не удалось получить достаточное число уникальных примеров")
    rows = []
    for label, samples in groups.items():
        rng.shuffle(samples)
        n_train, n_val = int(len(samples) * 0.7), int(len(samples) * 0.15)
        for i, facts in enumerate(samples):
            split = "train" if i < n_train else "validation" if i < n_train + n_val else "test"
            rows.append({"id": f"synthetic_{len(rows) + 1:04d}", "split": split, "label": label,
                         **{name: "" if name not in facts else str(int(facts[name])) for name in names}})
    rng.shuffle(rows)
    return rows


def load_rows(path, names):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", "split", "label", *names]:
            raise ValueError("Колонки датасета не соответствуют базе знаний")
        rows = list(reader)
    splits = {key: [] for key in ("train", "validation", "test")}
    seen = set()
    for row in rows:
        if row["label"] not in CLASSES or row["split"] not in splits or any(row[name] not in ("", "0", "1") for name in names):
            raise ValueError("Некорректная строка датасета")
        features = tuple(encode({name: {"": None, "0": False, "1": True}[row[name]] for name in names}, names))
        if features in seen:
            raise ValueError("Повтор векторов признаков: возможна утечка между выборками")
        seen.add(features)
        splits[row["split"]].append((list(features), row["label"]))
    if any(set(label for _, label in samples) != set(CLASSES) for samples in splits.values()):
        raise ValueError("Каждая выборка должна содержать все классы")
    return splits


def train_all(seed=42, epochs=100):
    expert = ExpertSystem(state_dir=None)
    knowledge = expert.knowledge
    names = fact_names(knowledge)
    directory = ROOT / "data"
    rows = generate(knowledge, seed)
    path = directory / "dataset.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "split", "label", *names], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    splits = load_rows(path, names)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = {"version": 1, "seed": seed, "synthetic": True, "scope": SCOPE,
                "dataset_sha256": digest, "knowledge_sha256": knowledge_digest(knowledge),
                "facts": names, "features": [name + suffix for name in names for suffix in ("__known", "__true")],
                "classes": CLASSES, "count": len(rows), "counts": {s: dict(Counter(y for _, y in samples)) for s, samples in splits.items()},
                "split": "Stratified 70/15/15, unique effective fact vectors; seed-shuffled within class",
                "labels": "ExpertSystem decision on generated three-valued facts; no real-world outcomes"}
    save_json(directory / "dataset.metadata.json", metadata)
    models = {"bayes": train_bayes(splits["train"], len(names) * 2),
              "neural": train_neural(splits["train"], splits["validation"], len(names) * 2, seed, epochs)}
    majority = Counter(y for _, y in splits["train"]).most_common(1)[0][0]
    metrics = {"version": 1, "scope": SCOPE, "dataset_sha256": digest, "knowledge_sha256": metadata["knowledge_sha256"],
               "majority_class": majority, "baseline_test_accuracy": sum(y == majority for _, y in splits["test"]) / len(splits["test"])}
    for key, model in models.items():
        model.update(version=1, classes=CLASSES, fact_names=names, dataset_sha256=digest, knowledge_sha256=metadata["knowledge_sha256"])
        save_json(ROOT / "models" / (key + ".json"), model)
        metrics[key] = {split: evaluate(model, samples) for split, samples in splits.items()}
    save_json(ROOT / "models/metrics.json", metrics)
    return metrics
