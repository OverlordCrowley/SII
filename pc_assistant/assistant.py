"""Разбор русскоязычного запроса и формирование объяснимого ответа."""

import re

from .expert import ExpertSystem


def normalize(text):
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е").replace("–", "-").replace("—", "-")).strip()


def analyze_query(text, symptoms):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Введите запрос длиной от 1 до 2000 символов")
    text = normalize(text)
    candidates = []
    for name, definition in symptoms.items():
        for key, value in (("true", True), ("false", False)):
            for phrase in definition[key]:
                phrase = normalize(phrase)
                for match in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text):
                    actual_value = value
                    # Отрицание короткого положительного синонима вне длинной фразы.
                    if re.search(r"\bне\s+$", text[:match.start()]) and key == "true":
                        actual_value = not value
                    candidates.append((match.start(), match.end(), name, actual_value, match.group()))
    accepted, occupied = [], []
    for item in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        start, end = item[:2]
        if any(start < right and end > left for left, right in occupied):
            continue
        accepted.append(item)
        occupied.append((start, end))
    values, fragments = {}, []
    for start, end, name, value, phrase in sorted(accepted):
        if name in values and values[name] != value:
            raise ValueError(f"Противоречивые сведения: {symptoms[name]['label']}. Уточните запрос")
        values[name] = value
        fragments.append({"symptom": name, "value": value, "phrase": phrase, "span": [start, end]})
    return {"facts": values, "recognized": fragments,
            "note": "Разбор по словарю фраз. Нераспознанные сведения не превращаются в факты."}


def format_proof(proof, labels, indent=0):
    name = labels.get(proof["fact"], proof["fact"])
    value = {True: "да", False: "нет", None: "неизвестно"}[proof["value"]]
    suffix = f" [{proof['rule']}: {proof['condition']}]" if "rule" in proof else " [ввод пользователя]"
    lines = ["  " * indent + f"{name} = {value}{suffix}"]
    for child in proof.get("children", []):
        lines.extend(format_proof(child, labels, indent + 1))
    return lines


def format_answer(result, knowledge):
    labels = {name: data["label"] for name, data in knowledge["symptoms"].items()}
    labels.update({name: data["label"] for name, data in knowledge["diagnoses"].items()})
    device = result["device"]
    lines = ["ИИ-помощник по диагностике компьютера", f"Устройство: {device['slots'].get('name', device['name'])}",
             f"Известных симптомов: {len(result['input_facts'])} из {len(knowledge['symptoms'])}"]
    if not result["diagnoses"]:
        lines.append("По правилам пока недостаточно сведений для вывода.")
    for diagnosis in result["diagnoses"]:
        lines += ["", diagnosis["label"], "Объяснение:"]
        lines += format_proof(diagnosis["explanation"], labels)
        lines.append("Рекомендуемые действия:")
        lines += [f"  {index}. {action}" for index, action in enumerate(diagnosis["actions"], 1)]
        lines.append("Объект фреймовой модели: " + diagnosis["component_frame"]["slots"].get("name", diagnosis["component"]))
    models = result["models"]
    if models.get("available"):
        lines += ["", "Учебный рейтинг моделей:"]
        for key, title in (("bayes", "Байес"), ("neural", "Нейросеть")):
            best = models[key][0]
            lines.append(f"  {title}: {best['label']} (оценка {best['score']:.1%})")
        lines.append(models["note"])
    if result["questions"]:
        lines += ["", "Можно уточнить (да / нет / неизвестно):"]
        lines += ["  " + q["label"] + "?" for q in result["questions"]]
    lines += ["", result["scope"]]
    return "\n".join(lines)


class Assistant:
    def __init__(self, expert=None):
        self.expert = expert or ExpertSystem()

    def ask(self, text, device="office_pc", extra_facts=None):
        analysis = analyze_query(text, self.expert.knowledge["symptoms"])
        facts = dict(analysis["facts"])
        for name, value in (extra_facts or {}).items():
            if name in facts and facts[name] != value:
                raise ValueError(f"Текст и явный факт {name} противоречат друг другу")
            facts[name] = value
        result = self.expert.diagnose(facts, device)
        result["query_analysis"] = analysis
        result["answer"] = format_answer(result, self.expert.knowledge)
        return result
