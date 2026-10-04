"""Проверки логики, границ ввода, данных и интеграции.

Запуск: python -m unittest discover -s tests -v
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from pc_assistant.assistant import Assistant, analyze_query
from pc_assistant.cli import DEMO_CASES
from pc_assistant.dataset import read_dataset
from pc_assistant.expert import ExpertSystem
from pc_assistant.frames import FrameStore
from pc_assistant.knowledge import ROOT, load_knowledge
from pc_assistant.logic import LogicalEngine, evaluate, parse, truth_table
from pc_assistant.network import SemanticNetwork
from pc_assistant.production import ProductionEngine


KNOWLEDGE = load_knowledge()


class LogicTests(unittest.TestCase):
    def test_truth_tables_and_precedence(self):
        self.assertTrue(evaluate("a OR b AND NOT c", {"a": False, "b": True, "c": False}))
        self.assertFalse(evaluate("(a OR b) AND c", {"a": True, "b": False, "c": False}))
        rows = truth_table("a AND (b OR NOT c)")
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(row["result"] for row in rows), 3)

    def test_unknown_is_not_false(self):
        self.assertIsNone(evaluate("NOT a", {}))
        self.assertIsNone(evaluate("a AND b", {"a": True}))
        self.assertFalse(evaluate("a AND b", {"a": False}))
        self.assertTrue(evaluate("a OR b", {"a": True}))

    def test_input_values_are_boolean(self):
        for value in (1, 0, "false", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluate("a", {"a": value})

    def test_invalid_expressions(self):
        for text in ("", "a; import os", "a b", "(a AND b", "a AND", ")", "a + b"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse(text)

    def test_cycles_terminate(self):
        rules = [{"id": "1", "if": "a", "then": "b"}, {"id": "2", "if": "b", "then": "a"}]
        self.assertIsNone(LogicalEngine(rules).query("a", {}))
        self.assertEqual(ProductionEngine(rules).run({})["trace"], [])
        self.assertTrue(LogicalEngine(rules).query("b", {"a": True}))

    def test_nonmonotonic_negation_rejected(self):
        rules = [{"id": "1", "if": "NOT a", "then": "b"}, {"id": "2", "if": "seed", "then": "a"}]
        with self.assertRaises(ValueError):
            ProductionEngine(rules)

    def test_forward_backward_agree(self):
        forward = ProductionEngine(KNOWLEDGE["rules"])
        backward = LogicalEngine(KNOWLEDGE["rules"])
        for _, query in DEMO_CASES:
            facts = analyze_query(query, KNOWLEDGE["symptoms"])["facts"]
            derived = forward.run(facts)["facts"]
            for goal in KNOWLEDGE["diagnoses"]:
                with self.subTest(query=query, goal=goal):
                    self.assertEqual(derived.get(goal) is True, backward.query(goal, facts) is True)

    def test_chain_explanation(self):
        engine = ProductionEngine(KNOWLEDGE["rules"])
        result = engine.run({"power_on": True, "screen_on": False, "beeps": True})
        proof = engine.explain("ram_failure", result)
        self.assertEqual(proof["rule"], "R03")
        self.assertEqual(proof["children"][0]["rule"], "R02")
        self.assertIs(proof["children"][0]["children"][1]["value"], False)

    def test_or_explains_only_supported_branch(self):
        for engine in (LogicalEngine(KNOWLEDGE["rules"]), ProductionEngine(KNOWLEDGE["rules"])):
            facts = {"high_temp": True, "shutdowns": True}
            proof = (engine.explain("overheating", facts) if isinstance(engine, LogicalEngine)
                     else engine.explain("overheating", engine.run(facts)))
            self.assertEqual({p["fact"] for p in proof["children"]}, {"high_temp", "shutdowns"})

    def test_conflicting_conclusion_rejected(self):
        with self.assertRaises(ValueError):
            ProductionEngine(KNOWLEDGE["rules"]).run({"power_on": False, "fan_spins": False, "power_failure": False})

    def test_negated_compound_explanation(self):
        rules = [{"id": "1", "if": "NOT (a AND b)", "then": "goal"}]
        facts = {"a": False}
        direct = ProductionEngine(rules)
        for proof in (LogicalEngine(rules).explain("goal", facts), direct.explain("goal", direct.run(facts))):
            self.assertEqual(proof["children"], [{"fact": "a", "value": False, "source": "input"}])


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.store = FrameStore(KNOWLEDGE["frames"])

    def test_inheritance_and_origins(self):
        description = self.store.describe("office_pc")
        self.assertEqual(description["slots"]["ram_gb"], 16)
        self.assertEqual(description["origins"]["os"], "computer")
        self.assertTrue(self.store.get("student_laptop", "has_battery"))

    def test_local_override_preserves_parent(self):
        self.store.set("office_pc", "ram_gb", 24)
        self.assertEqual(self.store.get("office_pc", "ram_gb"), 24)
        self.assertEqual(self.store.get("computer", "ram_gb"), 8)
        self.assertEqual(KNOWLEDGE["frames"]["office_pc"]["slots"]["ram_gb"], 16)

    def test_find_distinguishes_absent_and_null(self):
        self.assertEqual(self.store.find("not_a_slot", None), [])
        self.assertEqual(self.store.find("has_battery", 0), [])
        self.assertIn("student_laptop", self.store.find("has_battery", True))

    def test_invalid_slot_values(self):
        for value in (0, -1, True, "24"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.set("office_pc", "ram_gb", value)

    def test_create_and_rollback(self):
        self.store.create("new_pc", "desktop", {"ram_gb": 24})
        self.assertEqual(self.store.get("new_pc", "os"), "Windows")
        with self.assertRaises(ValueError):
            self.store.create("bad_pc", "desktop", {"ram_gb": -1})
        self.assertNotIn("bad_pc", self.store.frames)

    def test_cycle_and_missing_parent(self):
        for frames in ({"a": {"parent": "a", "slots": {}}}, {"a": {"parent": "missing", "slots": {}}}):
            with self.assertRaises(ValueError):
                FrameStore(frames)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.network = SemanticNetwork(KNOWLEDGE["network"])

    def test_shortest_directed_path(self):
        path = self.network.path("disk_noise", "storage")
        self.assertEqual([e[1] for e in path], ["indicates", "affects"])
        self.assertIsNone(self.network.path("device", "laptop"))
        self.assertEqual(self.network.path("laptop", "laptop"), [])

    def test_inverse_and_relation_filter(self):
        path = self.network.path("device", "laptop", undirected=True)
        self.assertEqual(len(path), 2)
        self.assertTrue(all(e[1] == "inverse:is-a" for e in path))
        self.assertIsNone(self.network.path("disk_noise", "storage", relation="is-a"))

    def test_related_depth_and_cycles(self):
        self.network.add_edge("device", "related-to", "laptop")
        values = self.network.related("laptop", depth=5)
        self.assertEqual({v["id"] for v in values}, {"computer", "device", "operating_system"})
        with self.assertRaises(ValueError):
            self.network.related("laptop", depth=0)

    def test_new_nodes_and_duplicate_edges(self):
        self.network.add_node("printer", "Принтер")
        before = len(self.network.edges)
        for _ in range(2):
            self.network.add_edge("printer", "connected-to", "computer")
        self.assertEqual(len(self.network.edges), before + 1)
        with self.assertRaises(ValueError):
            self.network.add_edge("missing", "part-of", "computer")


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.assistant = Assistant(ExpertSystem(use_models=False))

    def test_demo_scenarios(self):
        expected = ["power_failure", "overheating", "disk_failure", "ram_failure", "network_failure", "software_issue", None]
        for (name, query), diagnosis in zip(DEMO_CASES, expected):
            result = self.assistant.ask(query)
            with self.subTest(name=name):
                self.assertTrue(all(d["logical_verified"] for d in result["diagnoses"]))
                if diagnosis:
                    self.assertIn(diagnosis, [d["id"] for d in result["diagnoses"]])
                else:
                    self.assertEqual(result["diagnoses"], [])
                    self.assertIn("beeps", [q["id"] for q in result["questions"]])

    def test_negative_phrases_and_overlap(self):
        analysis = analyze_query("Компьютер не включается, не перегревается и не тормозит", KNOWLEDGE["symptoms"])
        self.assertEqual(analysis["facts"], {"power_on": False, "high_temp": False, "slow": False})

    def test_conflicting_text_and_explicit_facts(self):
        with self.assertRaises(ValueError):
            self.assistant.ask("тормозит и не тормозит")
        with self.assertRaises(ValueError):
            self.assistant.ask("тормозит", extra_facts={"slow": False})

    def test_unrecognized_query(self):
        result = self.assistant.ask("Здравствуйте, помогите")
        self.assertEqual(result["input_facts"], {})
        self.assertEqual(result["diagnoses"], [])
        self.assertTrue(result["questions"])

    def test_unknown_symptom_and_bad_values(self):
        for facts in ({"mystery": True}, {"slow": 1}):
            with self.assertRaises(ValueError):
                self.assistant.expert.diagnose(facts)


class DataModelTests(unittest.TestCase):
    def test_group_split_has_no_leakage(self):
        rows, metadata = read_dataset()
        self.assertEqual(len(rows), 1260)
        groups = {split: {row["group"] for row in rows if row["split"] == split}
                  for split in ("train", "validation", "test")}
        self.assertFalse(groups["train"] & groups["test"])
        self.assertFalse(groups["train"] & groups["validation"])
        self.assertFalse(groups["validation"] & groups["test"])
        self.assertEqual(len(metadata["features"]), 18)

    def test_metrics_recomputed_from_saved_weights(self):
        import numpy as np
        from pc_assistant.ml import BernoulliNB, NeuralNetwork, arrays, metrics
        rows, metadata = read_dataset()
        report = json.loads((ROOT / "models" / "metrics.json").read_text(encoding="utf-8"))
        x, y = arrays(rows, metadata["features"], metadata["labels"], "test")
        for name, cls in (("bayes", BernoulliNB), ("neural", NeuralNetwork)):
            artifact = json.loads((ROOT / "models" / f"{name}.json").read_text(encoding="utf-8"))
            model = cls.restore(artifact["model"])
            probabilities = model.predict_proba(x)
            self.assertTrue(np.allclose(probabilities.sum(axis=1), 1))
            actual = metrics(y, probabilities.argmax(axis=1), metadata["labels"])
            self.assertEqual(actual, report[name])
            self.assertGreater(actual["accuracy"], report["majority_baseline_accuracy"])

    def test_models_handle_missing_observations(self):
        from pc_assistant.ml import rank_models
        self.assertFalse(rank_models({"high_temp": True}, KNOWLEDGE)["available"])
        result = rank_models({"high_temp": True, "fan_loud": True, "shutdowns": True}, KNOWLEDGE)
        self.assertEqual(result["source"], "synthetic")
        self.assertEqual(result["bayes"][0]["id"], "overheating")
        self.assertEqual(result["neural"][0]["id"], "overheating")


class PersistenceCliTests(unittest.TestCase):
    def test_persistence_round_trip(self):
        build = ROOT / ".build"
        build.mkdir(exist_ok=True)
        directory = (build / ("persistence-" + uuid.uuid4().hex)).resolve()
        directory.mkdir()
        try:
            self.assertTrue(directory.is_relative_to(build.resolve()))
            expert = ExpertSystem(state_dir=directory, use_models=False)
            expert.frames.set("office_pc", "ram_gb", 24)
            expert.save_frames()
            expert.network.add_node("printer", "Принтер")
            expert.network.add_edge("printer", "connected-to", "computer")
            expert.save_network()
            restored = ExpertSystem(state_dir=directory, use_models=False)
            self.assertEqual(restored.frames.get("office_pc", "ram_gb"), 24)
            self.assertEqual(len(restored.network.path("printer", "device")), 2)
        finally:
            for filename in ("frames.json", "network.json"):
                (directory / filename).unlink(missing_ok=True)
            directory.rmdir()

    def test_cli_json_and_exit_code(self):
        process = subprocess.run([sys.executable, "-m", "pc_assistant", "--no-models", "ask", "перегревается, вентилятор шумит", "--json"],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["diagnoses"][0]["id"], "overheating")
        invalid = subprocess.run([sys.executable, "-m", "pc_assistant", "ask", "--facts", '{"slow":1}'],
                                 cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("true или false", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
