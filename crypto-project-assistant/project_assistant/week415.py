"""Исполняемый показ недель 4–15. Реальную защиту проводит студент."""

import hashlib
import json
from pathlib import Path
import tempfile

from .assistant import ProjectAssistant, markdown_report
from .dataset import load_rows
from .frames import FrameStore
from .knowledge import ROOT, load_knowledge
from .ml import ModelStore, evaluate, fact_names
from .network import SemanticNetwork
from .week48 import demonstrate as week48


def demonstrate():
    week48()
    knowledge = load_knowledge()
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)
        print("\nНЕДЕЛЯ 9: поиск и изменение свойств фреймов")
        frames = FrameStore(knowledge["frames"])
        frames.create("demo_protocol", "defi")
        frames.set("demo_protocol", "focus", "Учебный протокол")
        frames.save(state / "frames.json")
        restored = FrameStore.load(state / "frames.json", {})
        assert restored.find("focus", "Учебный протокол") == ["demo_protocol"]
        assert restored.get("demo_protocol", "quote_currency") == "USD"
        print(json.dumps(restored.describe("demo_protocol"), ensure_ascii=False))
        print("\nНЕДЕЛЯ 10: добавление узлов и связей, поиск BFS")
        network = SemanticNetwork(knowledge["network"])
        network.add_node("demo_protocol", "Учебный протокол")
        network.add_edge("project", "example", "demo_protocol")
        network.save(state / "network.json")
        restored_network = SemanticNetwork.load(state / "network.json", {})
        assert restored_network.path("project", "demo_protocol") == [["project", "example", "demo_protocol"]]
        print("Путь:", restored_network.path("project", "demo_protocol"))
        print("\nНЕДЕЛЯ 11: датасет и классификатор")
        models = ModelStore(knowledge)
        assert len(models.models) == 2, models.reason
        splits = load_rows(ROOT / "data/dataset.csv", fact_names(knowledge))
        assert hashlib.sha256((ROOT / "data/dataset.csv").read_bytes()).hexdigest() == models.metrics["dataset_sha256"]
        for key, week in (("bayes", 11), ("neural", 12)):
            print(f"НЕДЕЛЯ {week}: {key}")
            measured = evaluate(models.models[key], splits["test"])
            assert measured == models.metrics[key]["test"]
            print(f"Test: {measured['count']}; accuracy={measured['accuracy']:.4f}; macro-F1={measured['macro_f1']:.4f}")
            print("Матрица ошибок:", measured["confusion_matrix"])
        print("\nНЕДЕЛЯ 13: интегрированная экспертная система")
        assistant = ProjectAssistant(state)
        result = assistant.analyze({"name": "BTC", "type": "demo_protocol", "facts": {"chain_asset_verified": False}})
        assert result["frame"]["slots"]["focus"] == "Учебный протокол"
        assert any(item["id"] == "demo_protocol" for item in result["related"])
        assert result["status"] == "blocked" and len(result["recognition"]["predictions"]) == 2
        print(result["summary"])
        print("\nНЕДЕЛЯ 14: запрос → анализ → поиск решения → ответ")
        assert result["project"]["name"] == "Bitcoin" and result["project"]["symbol"] == "BTC"
        assert "R10" in markdown_report(result) and result["next_actions"]
        print(markdown_report(result).split("## Рекомендации")[1].split("## Числа")[0])
        print("Дополнительно: локальная Qwen через llama.cpp — local-ai check и ask BTC --local-ai.")
        print("Этот показ не требует запуска языковой модели; её фактическая проверка описана в docs/LOCAL_AI_QA_2026_10_07.md.")
    print("\nНЕДЕЛЯ 15: единый запуск, README, материалы защиты")
    for relative in ("README.md", "docs/WEEKS_04_15.md", "docs/DEFENSE.md", "docs/LOCAL_AI.md",
                     "docs/LOCAL_AI_QA_2026_10_07.md", "presentation/Cryptoscope_Weeks_04_15_Local_AI.pptx"):
        assert (ROOT / relative).is_file(), relative
    print("Презентация: presentation/Cryptoscope_Weeks_04_15_Local_AI.pptx")
    print("Реальная защита: студент проводит её с преподавателем; программа не подтверждает её проведение.")
    print("\nИсполняемые требования недель 4–14 проверены. Материалы недели 15 перечислены отдельно.")
