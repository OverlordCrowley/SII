"""Воспроизводимый синтетический датасет для учебной классификации.

Метки задаёт генератор профилей, а не экспертные правила.
Одинаковые векторы признаков всегда попадают в одну часть выборки.
"""

import csv
import hashlib
import json
import random

from .knowledge import ROOT, load_knowledge, save_json

PROFILES = {
    "power_failure": {"power_on": .08, "fan_spins": .08, "screen_on": .05, "boot_ok": .05},
    "display_failure": {"power_on": .97, "fan_spins": .95, "screen_on": .04, "beeps": .05, "boot_ok": .7},
    "ram_failure": {"power_on": .94, "fan_spins": .9, "screen_on": .25, "beeps": .9, "boot_ok": .12, "random_restarts": .65},
    "overheating": {"power_on": .98, "fan_spins": .9, "screen_on": .95, "boot_ok": .95, "high_temp": .95, "fan_loud": .9, "shutdowns": .75, "random_restarts": .35},
    "disk_failure": {"power_on": .98, "fan_spins": .95, "screen_on": .95, "boot_ok": .45, "slow": .85, "disk_noise": .75, "disk_errors": .92, "boot_error": .6},
    "network_failure": {"power_on": .98, "fan_spins": .95, "screen_on": .95, "boot_ok": .98, "internet_down": .96, "wifi_disconnected": .65},
    "software_issue": {"power_on": .98, "fan_spins": .95, "screen_on": .95, "boot_ok": .94, "slow": .8, "many_apps": .8, "crashes": .75, "recent_update": .65}
}


def generate(seed=42, per_class=180, output=None):
    if per_class < 30:
        raise ValueError("Нужно не менее 30 примеров на класс")
    knowledge = load_knowledge()
    features = list(knowledge["symptoms"])
    if set(PROFILES) != set(knowledge["diagnoses"]):
        raise ValueError("Профили датасета не соответствуют диагнозам")
    rng, rows = random.Random(seed), []
    for label in knowledge["diagnoses"]:
        profile = PROFILES[label]
        for _ in range(per_class):
            vector = [int(rng.random() < profile.get(feature, .06)) for feature in features]
            # Групповой split исключает утечку одинаковых векторов.
            key = "".join(map(str, vector))
            bucket = int(hashlib.sha256((str(seed) + ":" + key).encode()).hexdigest()[:8], 16) % 100
            split = "train" if bucket < 70 else ("validation" if bucket < 85 else "test")
            rows.append({**dict(zip(features, vector)), "label": label, "split": split, "group": key})
    rng.shuffle(rows)
    output = output or ROOT / "data" / "dataset.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*features, "label", "split", "group"])
        writer.writeheader()
        writer.writerows(rows)
    metadata = {"source": "synthetic", "seed": seed, "rows": len(rows), "per_class": per_class,
                "features": features, "labels": list(knowledge["diagnoses"]), "profiles": PROFILES,
                "split_method": "sha256(seed:binary_vector), grouped 70/15/15",
                "limitations": "Синтетические независимые признаки. Метрики не оценивают реальную диагностику компьютеров.",
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    save_json(output.with_suffix(".metadata.json"), metadata)
    return metadata


def read_dataset(path=None):
    path = path or ROOT / "data" / "dataset.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    metadata = json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
        raise ValueError("Датасет изменён. Пересоздайте метаданные и переобучите модели")
    if metadata["features"] != list(load_knowledge()["symptoms"]):
        raise ValueError("Признаки базы знаний и датасета не совпадают")
    groups = {}
    for row in rows:
        if row["label"] not in metadata["labels"] or row["split"] not in {"train", "validation", "test"}:
            raise ValueError("Неверная метка или часть выборки")
        if any(row[f] not in {"0", "1"} for f in metadata["features"]):
            raise ValueError("Признаки должны быть 0 или 1")
        vector = "".join(row[f] for f in metadata["features"])
        if vector != row["group"]:
            raise ValueError("Неверный идентификатор группы")
        if vector in groups and groups[vector] != row["split"]:
            raise ValueError("Утечка: одинаковый вектор находится в разных частях")
        groups[vector] = row["split"]
    for split in ("train", "validation", "test"):
        if {r["label"] for r in rows if r["split"] == split} != set(metadata["labels"]):
            raise ValueError(f"В части {split} отсутствует класс")
    return rows, metadata
