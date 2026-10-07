"""Единая экспертная система: знания, два вида вывода, фреймы и сеть."""

from pathlib import Path

from .frames import FrameStore
from .knowledge import ROOT, load_knowledge
from .logic import LogicalEngine
from .network import SemanticNetwork
from .production import ProductionEngine


def decision(knowledge, result):
    severities = {rule.get("severity") for rule in knowledge["rules"] if result["facts"].get(rule["then"]) is True}
    if "critical" in severities:
        return "blocked"
    if "high" in severities:
        return "revise"
    return "research" if result["facts"].get("research_ready") is True else "clarify"


def applicable_facts(knowledge, facts):
    return {key: value for key, value in facts.items() if value is not None
            and not (key in knowledge["computed"] and facts.get("has_token") is False)
            and facts.get(knowledge["criteria"].get(key, {}).get("applies_if")) is not False}


class ExpertSystem:
    def __init__(self, state_dir=ROOT / ".state"):
        self.knowledge = load_knowledge()
        self.logical = LogicalEngine(self.knowledge["rules"])
        self.production = ProductionEngine(self.knowledge["rules"])
        if state_dir is None:
            self.frames = FrameStore(self.knowledge["frames"])
            self.network = SemanticNetwork(self.knowledge["network"])
        else:
            state_dir = Path(state_dir)
            self.frames = FrameStore.load(state_dir / "frames.json", self.knowledge["frames"])
            self.network = SemanticNetwork.load(state_dir / "network.json", self.knowledge["network"])

    def infer(self, facts):
        facts = applicable_facts(self.knowledge, facts)
        result = self.production.run(facts)
        proofs = {}
        for event in result["trace"]:
            proof = self.logical.explain(event["conclusion"], facts)
            if proof["value"] is not True:
                raise ValueError("Прямой и обратный вывод не согласуются")
            proofs[event["conclusion"]] = proof
        return {**result, "proofs": proofs, "status": decision(self.knowledge, result)}
