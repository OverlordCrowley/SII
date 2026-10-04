"""Загрузка общей базы знаний из редактируемого JSON."""

import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def save_json(path, data):
    """Атомарная запись: незавершённая запись не разрушает старый файл."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
