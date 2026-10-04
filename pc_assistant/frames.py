"""Фреймы: слоты, значения по умолчанию и одиночное наследование."""

from copy import deepcopy
import re


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
        self.frames[name] = {"parent": parent, "slots": deepcopy(slots or {})}
        return self.describe(name)
