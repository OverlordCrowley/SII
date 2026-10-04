"""Объединение правил, фреймов, сети и обученных моделей."""

from pathlib import Path

from .frames import FrameStore
from .knowledge import ROOT, load_knowledge
from .logic import LogicalEngine, atoms, evaluate_node, validate_facts
from .network import SemanticNetwork
from .production import ProductionEngine


class ExpertSystem:
    def __init__(self, state_dir=None, use_models=True):
        self.knowledge = load_knowledge()
        self.state_dir = Path(state_dir) if state_dir is not None else ROOT / ".state"
        self.frames = FrameStore.load(self.state_dir / "frames.json", self.knowledge["frames"])
        self.network = SemanticNetwork.load(self.state_dir / "network.json", self.knowledge["network"])
        self.logical = LogicalEngine(self.knowledge["rules"])
        self.production = ProductionEngine(self.knowledge["rules"])
        self.use_models = use_models

    def diagnose(self, facts, device="office_pc"):
        validate_facts(facts)
        unknown = set(facts) - self.knowledge["symptoms"].keys()
        if unknown:
            raise ValueError("Неизвестные симптомы: " + ", ".join(sorted(unknown)))
        frame = self.frames.describe(device)
        result = self.production.run(facts)
        diagnoses = []
        for name, definition in self.knowledge["diagnoses"].items():
            if result["facts"].get(name) is not True:
                continue
            diagnoses.append({"id": name, **definition,
                              "explanation": self.production.explain(name, result),
                              "logical_verified": self.logical.query(name, facts) is True,
                              "component_frame": self.frames.describe(definition["component"]),
                              "network_path": self.network.path(name, definition["component"])})
        needed = []
        for rule, node in self.logical.rules:
            if rule["then"] not in self.knowledge["diagnoses"]:
                continue
            names = atoms(node)
            known_names = names & result["facts"].keys()
            if known_names and evaluate_node(node, result["facts"].get) is None:
                for name in sorted(names - facts.keys()):
                    if name in self.knowledge["symptoms"] and name not in needed:
                        needed.append(name)
        # Вопросы упорядочиваются по правилам, а ответы не подставляются автоматически.
        if not needed:
            needed = [name for name in self.knowledge["symptoms"] if name not in facts]
        models = {"available": False, "reason": "Модели отключены"}
        if self.use_models:
            try:
                from .ml import rank_models
                models = rank_models(facts, self.knowledge)
            except (ImportError, OSError, ValueError, KeyError, TypeError) as error:
                models = {"available": False, "reason": str(error)}
        return {"device": frame, "input_facts": dict(facts), "diagnoses": diagnoses,
                "trace": result["trace"], "derived_facts": {key: value for key, value in result["facts"].items()
                                                           if key not in facts},
                "questions": [{"id": name, "label": self.knowledge["symptoms"][name]["label"]}
                              for name in needed[:5]],
                "models": models,
                "scope": "Учебная предварительная диагностика. Несколько неисправностей могут иметь общие симптомы."}

    def save_frames(self):
        self.frames.save(self.state_dir / "frames.json")

    def save_network(self):
        self.network.save(self.state_dir / "network.json")
