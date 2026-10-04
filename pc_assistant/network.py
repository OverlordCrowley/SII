"""Семантическая сеть с типизированными рёбрами и поиском BFS."""

from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import re

from .knowledge import save_json


class SemanticNetwork:
    def __init__(self, data):
        self.nodes = deepcopy(data["nodes"])
        self.edges = []
        for source, relation, target in data["edges"]:
            self.add_edge(source, relation, target)

    def add_node(self, name, label):
        if not re.fullmatch(r"[a-z_][a-z_0-9]{0,63}", name) or not str(label).strip():
            raise ValueError("Недопустимое имя или пустое описание узла")
        if name in self.nodes:
            raise ValueError("Узел уже существует")
        self.nodes[name] = str(label)

    def add_edge(self, source, relation, target):
        if source not in self.nodes or target not in self.nodes:
            raise ValueError("Оба конца связи должны существовать")
        if not re.fullmatch(r"[a-z][a-z-]{0,31}", relation):
            raise ValueError("Недопустимое имя отношения")
        edge = [source, relation, target]
        if edge not in self.edges:
            self.edges.append(edge)

    def adjacent(self, node, relation=None, undirected=False):
        if node not in self.nodes:
            raise ValueError(f"Узел {node} не найден")
        for source, kind, target in self.edges:
            if relation is not None and kind != relation:
                continue
            if source == node:
                yield [source, kind, target]
            if undirected and target == node:
                yield [target, "inverse:" + kind, source]

    def path(self, source, target, relation=None, undirected=False):
        if source not in self.nodes or target not in self.nodes:
            raise ValueError("Узел не найден")
        queue, visited = deque([(source, [])]), {source}
        while queue:
            node, path = queue.popleft()
            if node == target:
                return path
            for edge in self.adjacent(node, relation, undirected):
                if edge[2] not in visited:
                    visited.add(edge[2])
                    queue.append((edge[2], path + [edge]))
        return None

    def related(self, source, depth=1, relation=None, undirected=False):
        if not 1 <= depth <= 10:
            raise ValueError("Глубина поиска должна быть от 1 до 10")
        if source not in self.nodes:
            raise ValueError("Узел не найден")
        queue, visited, found = deque([(source, 0)]), {source}, []
        while queue:
            node, distance = queue.popleft()
            if distance == depth:
                continue
            for edge in self.adjacent(node, relation, undirected):
                if edge[2] not in visited:
                    visited.add(edge[2])
                    found.append({"id": edge[2], "label": self.nodes[edge[2]],
                                  "distance": distance + 1, "via": edge})
                    queue.append((edge[2], distance + 1))
        return found

    def save(self, path):
        save_json(path, {"version": 1, "nodes": self.nodes, "edges": self.edges})

    @classmethod
    def load(cls, path, defaults):
        path = Path(path)
        if not path.exists():
            return cls(defaults)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("Неизвестная версия сети")
        return cls(data)
