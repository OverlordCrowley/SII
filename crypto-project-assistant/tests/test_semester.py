"""Данные, реальные сохранённые модели и интеграция недель 9–14."""

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from project_assistant.__main__ import main
from project_assistant.assistant import ProjectAssistant, markdown_report
from project_assistant.dataset import generate, knowledge_digest, load_rows
from project_assistant.expert import ExpertSystem, decision
from project_assistant.knowledge import ROOT, load_knowledge
from project_assistant.ml import CLASSES, ModelStore, encode, evaluate, fact_names, probabilities, train_neural


class SemesterTests(unittest.TestCase):
    def test_dataset_has_no_duplicates_and_all_labels_match_rules(self):
        knowledge = load_knowledge()
        names = fact_names(knowledge)
        splits = load_rows(ROOT / "data/dataset.csv", names)
        expert = ExpertSystem(None)
        self.assertEqual(sum(map(len, splits.values())), 1340)
        for split, rows in splits.items():
            self.assertEqual({y for _, y in rows}, set(CLASSES))
            for x, label in rows:
                facts = {name: bool(x[2*i+1]) for i, name in enumerate(names) if x[2*i]}
                self.assertEqual(decision(knowledge, expert.production.run(facts)), label)
        metadata = json.loads((ROOT / "data/dataset.metadata.json").read_text())
        self.assertEqual(hashlib.sha256((ROOT / "data/dataset.csv").read_bytes()).hexdigest(), metadata["dataset_sha256"])
        self.assertEqual(metadata["knowledge_sha256"], knowledge_digest(knowledge))
        self.assertEqual(generate(knowledge), generate(knowledge))

    def test_saved_models_reproduce_holdout_metrics(self):
        knowledge = load_knowledge()
        store = ModelStore(knowledge)
        self.assertEqual(set(store.models), {"bayes", "neural"}, store.reason)
        test = load_rows(ROOT / "data/dataset.csv", fact_names(knowledge))["test"]
        for key, model in store.models.items():
            metric = evaluate(model, test)
            self.assertEqual(metric, store.metrics[key]["test"])
            self.assertGreater(metric["accuracy"], store.metrics["baseline_test_accuracy"])
            self.assertGreater(metric["macro_f1"], 0.8)
            for x, _ in test[:8]:
                ps = probabilities(model, x)
                self.assertAlmostEqual(sum(ps), 1)
                self.assertTrue(all(0 <= p <= 1 for p in ps))

    def test_changed_knowledge_disables_stale_models(self):
        knowledge = load_knowledge()
        knowledge["criteria"]["product_exists"]["weight"] += 1
        store = ModelStore(knowledge)
        self.assertEqual(store.models, {})
        self.assertIn("устарели", store.reason)

    def test_neural_training_learns_and_is_reproducible(self):
        rows = [([int(i == j) for j in range(4)], CLASSES[i]) for i in range(4)] * 8
        a = train_neural(rows, rows[:4], 4, epochs=60, hidden_size=8)
        b = train_neural(rows, rows[:4], 4, epochs=60, hidden_size=8)
        self.assertEqual(a, b)
        self.assertEqual(evaluate(a, rows[:4])["accuracy"], 1)

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = main(list(args))
        return code, output.getvalue()

    def test_frame_cli_changes_persist_and_are_used_by_assistant(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            prefix = ["frame", "--state", str(state / "frames.json")]
            for tail in (["create", "my_defi", "defi"], ["set", "my_defi", "focus", '"Изменённый фокус"']):
                code, output = self.cli(*prefix, *tail)
                self.assertEqual(code, 0, output)
            code, output = self.cli(*prefix, "find", "focus", '"Изменённый фокус"')
            self.assertEqual(json.loads(output), ["my_defi"])
            result = ProjectAssistant(state).analyze({"type": "my_defi", "name": "Ethereum"})
            self.assertEqual(result["frame"]["slots"]["focus"], "Изменённый фокус")
            self.assertEqual(result["frame"]["origins"]["quote_currency"], "crypto_project")
            self.assertEqual(result["project"]["symbol"], "ETH")
            before = (state / "frames.json").read_bytes()
            code, _ = self.cli(*prefix, "set", "my_defi", "focus", "false")
            self.assertEqual(code, 2)
            self.assertEqual((state / "frames.json").read_bytes(), before)

    def test_network_cli_roundtrip_and_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            prefix = ["network", "--state", str(state / "network.json")]
            for tail in (["add-node", "bridge", "Мост"], ["add-edge", "project", "has", "bridge"]):
                code, output = self.cli(*prefix, *tail)
                self.assertEqual(code, 0, output)
            code, output = self.cli(*prefix, "path", "project", "bridge")
            self.assertEqual(json.loads(output), [["project", "has", "bridge"]])
            result = ProjectAssistant(state).analyze({"name": "BTC"})
            self.assertTrue(any(item["id"] == "bridge" for item in result["related"]))
            self.assertEqual(self.cli(*prefix, "related", "project", "--depth", "11")[0], 2)

    def test_corrupt_state_has_a_clear_cli_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            for command, data in (("frame", {"version": 1}), ("frame", {"version": 1, "frames": {"a": {"slots": {}, "parent": {}}}}),
                                  ("network", {"version": 1}), ("network", {"version": 1, "nodes": {"a": "A"}, "edges": [["a"]]})):
                path.write_text(json.dumps(data))
                args = ["show", "defi"] if command == "frame" else ["nodes"]
                code, output = self.cli(command, "--state", str(path), *args)
                self.assertEqual(code, 2)
                self.assertIn("Ошибка", output)
                self.assertNotIn("Traceback", output)

    def test_models_are_advisory_and_use_applicable_facts(self):
        assistant = ProjectAssistant(None)
        result = assistant.analyze({"name": "Bitcoin", "facts": {"has_token": False, "chain_asset_verified": False}})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["recognition"]["predictions"]), 2)
        self.assertIn("Синтетические", markdown_report(result))
        alternate = assistant.analyze({"name": "BTC", "facts": {"has_token": False, "chain_asset_verified": False, "vesting_disclosed": False}})
        self.assertEqual(result["recognition"], alternate["recognition"])
        self.assertTrue(result["findings"][0]["proof"])


if __name__ == "__main__":
    unittest.main()
