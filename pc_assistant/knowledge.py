"""Загрузка общей базы знаний из редактируемого JSON."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_knowledge(path=None):
    data = json.loads(Path(path or ROOT / "data" / "knowledge.json").read_text(encoding="utf-8"))
    required = {"symptoms", "diagnoses", "rules", "frames", "network"}
    if not required <= data.keys():
        raise ValueError("База знаний содержит не все обязательные разделы")
    ids = [rule["id"] for rule in data["rules"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Идентификаторы правил должны быть уникальными")
    if not data["symptoms"] or not data["diagnoses"]:
        raise ValueError("База знаний не должна быть пустой")
    return data
