"""Фреймы: слоты, значения по умолчанию и одиночное наследование."""

from copy import deepcopy
import json
from pathlib import Path
import re

from .knowledge import save_json


class FrameStore:
    def __init__(self, frames):
        self.frames = deepcopy(frames)
        for name in self.frames:
            self.describe(name)

    def describe(self, name):
        slots, origins, visiting = {}, {}, set()

        def inherit(current):
            if current not in self.frames:
                raise ValueError(f"Фрейм {current} не найден")
            if current in visiting:
                raise ValueError("Цикл наследования фреймов")
            visiting.add(current)
            frame = self.frames[current]
            if frame.get("parent"):
                inherit(frame["parent"])
            if not isinstance(frame.get("slots"), dict):
                raise ValueError("Слоты фрейма должны быть словарём")
            for key, value in frame["slots"].items():
                slots[key] = deepcopy(value)
                origins[key] = current
            visiting.remove(current)

        inherit(name)
        return {"name": name, "parent": self.frames[name].get("parent"),
                "slots": slots, "origins": origins}

    def get(self, name, slot):
        values = self.describe(name)["slots"]
        if slot not in values:
            raise ValueError(f"Слот {slot} не найден")
        return values[slot]

    def create(self, name, parent, slots=None):
        if not re.fullmatch(r"[a-z_][a-z_0-9]{0,63}", name) or name in self.frames:
            raise ValueError("Имя фрейма недопустимо или уже используется")
        self.describe(parent)
        self.frames[name] = {"parent": parent, "slots": {}}
        try:
            for key, value in (slots or {}).items():
                self.set(name, key, value)
        except (ValueError, TypeError):
            del self.frames[name]
            raise
        return self.describe(name)

    def set(self, name, slot, value):
        if not re.fullmatch(r"[a-z_][a-z_0-9]{0,63}", slot):
            raise ValueError("Недопустимое имя слота")
        effective = self.describe(name)["slots"]
        if type(value) not in (str, int, float, bool, list, dict, type(None)):
            raise ValueError("Значение должно быть совместимо с JSON")
        if slot in effective and type(value) is not type(effective[slot]):
            raise ValueError(f"Тип слота {slot} должен быть {type(effective[slot]).__name__}")
        json.dumps(value, allow_nan=False)
        self.frames[name]["slots"][slot] = deepcopy(value)
        return self.describe(name)

    def find(self, slot, value):
        found = []
        for name in sorted(self.frames):
            slots = self.describe(name)["slots"]
            if slot in slots and type(slots[slot]) is type(value) and slots[slot] == value:
                found.append(name)
        return found

    def save(self, path):
        save_json(path, {"version": 1, "frames": self.frames})

    @classmethod
    def load(cls, path, defaults):
        path = Path(path)
        if not path.exists():
            return cls(defaults)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("Неизвестная версия сохранённых фреймов")
        return cls(data["frames"])
