"""Единая редактируемая база знаний и атомарное сохранение JSON."""

import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def save_json(path, data):
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


def load_knowledge():
    data = json.loads((ROOT / "data/knowledge.json").read_text(encoding="utf-8"))
    names = set(data["criteria"])
    conclusions = {r["then"] for r in data["rules"]}
    if names & conclusions or len({r["id"] for r in data["rules"]}) != len(data["rules"]):
        raise ValueError("Некорректные идентификаторы базы знаний")
    return data
