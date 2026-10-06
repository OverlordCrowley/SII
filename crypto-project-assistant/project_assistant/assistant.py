"""Анализ описания, объяснимый вывод и вопросы при неполных данных."""

from datetime import datetime, timezone
import re

from .frames import FrameStore
from .knowledge import load_knowledge
from .logic import LogicalEngine
from .network import SemanticNetwork
from .production import ProductionEngine
from .tokenomics import calculate


def text(value, name, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Поле «{name}» должно быть строкой длиной до {limit} символов")
    return value.strip()


def extract_claims(description, criteria):
    """Только явные фразы из словаря; конфликт требует уточнения."""
    normalized = description.lower().replace("ё", "е")
    claims, conflicts = {}, []
    for key, item in criteria.items():
        matches = []
        for value, phrases in ((False, item["negative"]), (True, item["positive"])):
            for phrase in phrases:
                pattern = r"(?<!\w)" + re.escape(phrase.replace("ё", "е")) + r"(?!\w)"
                matches.extend((m.start(), m.end(), value) for m in re.finditer(pattern, normalized))
        negative = [(start, end) for start, end, value in matches if value is False]
        matches = [m for m in matches if m[2] is False or not any(m[0] < end and m[1] > start for start, end in negative)]
        values = {m[2] for m in matches}
        if len(values) > 1:
            conflicts.append(key)
        elif values:
            start, end, value = matches[0]
            claims[key] = {"value": value, "evidence": description[start:end], "source": "Распознано из описания", "date": "", "origin": "description"}
    return claims, conflicts


class ProjectAssistant:
    def __init__(self):
        self.knowledge = load_knowledge()
        self.criteria = self.knowledge["criteria"]
        self.logical = LogicalEngine(self.knowledge["rules"])
        self.production = ProductionEngine(self.knowledge["rules"])
        self.frames = FrameStore(self.knowledge["frames"])
        self.network = SemanticNetwork(self.knowledge["network"])

    def profile(self, data):
        if not isinstance(data, dict):
            raise ValueError("Описание проекта должно быть объектом JSON")
        name = text(data.get("name", "Проект без названия"), "Название", 200) or "Проект без названия"
        description = text(data.get("description", ""), "Описание", 40000)
        kind = text(data.get("type", "crypto_project"), "Тип", 64)
        self.frames.describe(kind)
        explicit = data.get("facts", {})
        if not isinstance(explicit, dict) or set(explicit) - set(self.criteria):
            raise ValueError("Факты должны быть объектом с известными идентификаторами критериев")
        claims, conflicts = extract_claims(description, self.criteria)
        for key, item in explicit.items():
            if not isinstance(item, dict):
                item = {"value": item}
            value = item.get("value")
            if value is not None and type(value) is not bool:
                raise ValueError(f"Критерий {key}: допустимы true, false или null")
            date = text(item.get("date", ""), "Дата сведений", 10)
            if date:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                        raise ValueError("Неверный формат даты")
                    datetime.strptime(date, "%Y-%m-%d")
                except ValueError as error:
                    raise ValueError("Дата сведений должна иметь вид YYYY-MM-DD") from error
            claims[key] = {"value": value, "evidence": text(item.get("evidence", ""), "Основание", 2000),
                           "source": text(item.get("source", ""), "Источник", 500), "date": date, "origin": "input"}
        warnings = [f"Противоречивые фразы: «{self.criteria[key]['label']}». Уточните значение." for key in conflicts if key not in explicit]
        identity = {key: text(data.get(key, ""), key, 250) for key in ("symbol", "network", "contract")}
        tokenomics = calculate(data.get("market", {}))
        return {"name": name, "description": description, "type": kind, "facts": claims, **identity, "market": tokenomics["input"]}, warnings

    def analyze(self, data):
        profile, warnings = self.profile(data)
        facts = {key: item["value"] for key, item in profile["facts"].items() if item["value"] is not None}
        tokenomics = calculate(profile["market"])
        warnings.extend(tokenomics["warnings"])
        facts.update(tokenomics["facts"])
        result = self.production.run(facts)
        findings, strengths = [], []
        for rule in self.knowledge["rules"]:
            goal = rule["then"]
            if result["facts"].get(goal) is not True:
                continue
            proof = self.logical.explain(goal, facts)
            def mark_calculations(node):
                if node["fact"] in tokenomics["facts"]:
                    node["source"] = "calculation"
                for child in node.get("children", []):
                    mark_calculations(child)
            mark_calculations(proof)
            assert proof["value"] is True, "Прямой и обратный вывод не согласуются"
            entry = {"id": goal, "rule": rule["id"], "label": rule["label"], "proof": proof}
            if "severity" in rule:
                findings.append({**entry, "severity": rule["severity"], "action": rule["action"]})
            else:
                strengths.append(entry)
        order = {"critical": 0, "high": 1, "medium": 2}
        findings.sort(key=lambda item: (order[item["severity"]], item["rule"]))
        assessments, total, positive, unknown, known_count = [], 0, 0, 0, 0
        for key, item in self.criteria.items():
            claim = profile["facts"].get(key, {"value": None, "evidence": "", "source": "", "date": "", "origin": "missing"})
            active = facts.get(item.get("applies_if")) is not False
            value = claim["value"]
            weight = item["weight"] if active else 0
            total += weight
            positive += weight if value is True else 0
            unknown += weight if value is None else 0
            known_count += int(active and value is not None)
            assessments.append({"id": key, **item, **claim, "applicable": active})
        active_count = sum(item["applicable"] for item in assessments)
        score = {"min": round(positive / total * 100), "max": round((positive + unknown) / total * 100),
                 "coverage": round(known_count / active_count * 100), "known": known_count, "total": active_count}
        # ponytail: веса — прозрачная учебная эвристика; эмпирическая калибровка требует реальных исходов проектов.
        if any(item["severity"] == "critical" for item in findings):
            status, label = "blocked", "Сначала проверьте критичные сведения"
        elif any(item["severity"] == "high" for item in findings):
            status, label = "revise", "Исследование требует дополнения"
        elif result["facts"].get("research_ready"):
            status, label = "research", "Есть основа для углублённого сравнения"
        else:
            status, label = "clarify", "Нужно уточнить проект"
        missing = [item for item in assessments if item["applicable"] and item["value"] is None
                   and (not item.get("applies_if") or facts.get(item["applies_if"]) is not None)]
        priorities = {"chain_asset_verified": 100, "has_token": 90, "product_exists": 80, "problem_defined": 70, "users_active": 65}
        missing.sort(key=lambda item: (-priorities.get(item["id"], 0), -item["weight"], item["id"]))
        questions = [{"id": item["id"], "question": item["question"], "label": item["label"]} for item in missing]
        actions = [item["action"] for item in findings[:3]] + [item["question"] for item in questions[:3]]
        risk_text = f"Выявлено вопросов для доработки: {len(findings)}. " if findings else "По известным фактам проблемные правила не сработали. "
        summary = f"«{profile['name']}»: {label.lower()}. {risk_text}Известны {known_count} из {active_count} применимых критериев."
        if questions:
            summary += " Первый вопрос: " + questions[0]["question"]
        return {"project": profile, "generated_at": datetime.now(timezone.utc).isoformat(), "status": status, "status_label": label,
                "summary": summary, "score": score, "findings": findings, "strengths": strengths, "questions": questions,
                "next_actions": actions, "criteria": assessments, "warnings": warnings, "trace": result["trace"],
                "frame": self.frames.describe(profile["type"]), "related": self.network.related("project", depth=2), "tokenomics": tokenomics,
                "computed_labels": self.knowledge["computed"],
                "limits": "Анализ основан на введённых сведениях и учебных правилах. Источники не проверялись автоматически. Балл отражает проработанность анализа; он не оценивает доходность и не предсказывает цену токена."}


def proof_lines(proof, labels, level=0):
    value = {True: "ДА", False: "НЕТ", None: "НЕИЗВЕСТНО"}[proof["value"]]
    source = proof.get("rule") or ("расчёт по введённым числам" if proof.get("source") == "calculation" else "исходное утверждение")
    lines = ["  " * level + f"{labels.get(proof['fact'], proof['fact'])}: {value} [{source}]"]
    for child in proof.get("children", []):
        lines.extend(proof_lines(child, labels, level + 1))
    return lines


def markdown_report(result):
    labels = {item["id"]: item["label"] for item in result["criteria"]}
    labels.update({item["id"]: item["label"] for item in result["findings"] + result["strengths"]})
    labels.update(result["computed_labels"])
    clean = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    score = result["score"]
    lines = [f"# Анализ проекта: {clean(result['project']['name'])}", "", f"Дата анализа (UTC): {result['generated_at']}", "",
             result["summary"], "", f"Проработанность анализа: **{score['min']}–{score['max']} / 100**. Полнота сведений: **{score['coverage']}%**.", "",
             result["limits"], "", "## Идентификация проекта", "",
             f"Тип: {clean(result['project']['type'])}. Тикер: {clean(result['project']['symbol']) or 'не указан'}.",
             f"Сеть: {clean(result['project']['network']) or 'не указана'}. Контракт / нативный актив: {clean(result['project']['contract']) or 'не указан'}.",
             "", "## Описание", "", result["project"]["description"] or "Описание не введено.", "", "## Рекомендации", ""]
    lines.extend(f"{index}. {action}" for index, action in enumerate(result["next_actions"], 1))
    if not result["next_actions"]:
        lines.append("Сравните проект с аналогами на одну дату и проверьте введённые основания по первичным источникам.")
    numbers = result["tokenomics"]
    lines.extend(["", "## Числа и токеномика", "", f"Валюта: USD. Дата снимка: {numbers['input']['as_of'] or 'не указана'}. Источник: {numbers['input']['source'] or 'не указан'}.", ""])
    metric_labels = {"market_cap_usd":"Капитализация USD", "fdv_usd":"FDV USD", "calculated_market_cap_usd":"Капитализация по формуле USD", "calculated_fdv_usd":"FDV по формуле USD", "fdv_to_cap":"FDV / капитализация", "circulating_share_pct":"Доля обращения %", "unlock_share_pct":"Unlock / обращение %", "volume_to_cap_pct":"Объём 24ч / капитализация %"}
    for key,value in numbers["values"].items():
        lines.append(f"- {metric_labels[key]}: {'неизвестно' if value is None else format(value, '.8g')}")
    lines.extend(["", f"База FDV: {numbers['input']['fdv_basis']} supply. Дата unlock: {numbers['input']['unlock_date'] or 'не указана'}.", numbers["thresholds"], ""])
    lines.extend(["", "## Вывод и объяснения", ""])
    for item in result["findings"] + result["strengths"]:
        lines.extend([f"### {item['label']} ({item['rule']})", ""])
        if "action" in item:
            lines.extend([item["action"], ""])
        lines.extend(["```text", *proof_lines(item["proof"], labels), "```", ""])
    if not result["findings"] and not result["strengths"]:
        lines.append("Недостаточно сведений для вывода. Неизвестные критерии не считаются ложными.")
    lines.extend(["", "## Вопросы для уточнения", ""])
    lines.extend(f"- {item['question']}" for item in result["questions"])
    lines.extend(["", "## Введённые утверждения и основания", "", "| Критерий | Значение | Основание | Источник | Дата |", "|---|---|---|---|---|"])
    for item in result["criteria"]:
        value = "Не применяется" if not item["applicable"] else {True: "Да", False: "Нет", None: "Неизвестно"}[item["value"]]
        lines.append("| " + " | ".join(clean(v) for v in (item["label"], value, item["evidence"], item["source"], item["date"])) + " |")
    lines.extend(["", "## Метод оценки", "", "У каждого применимого критерия есть фиксированный вес в базе знаний. Нижняя граница = сумма весов известных положительных критериев / сумма применимых весов. Верхняя граница допускает положительные ответы по неизвестным критериям. Токеномика исключается из оценки при явном ответе, что собственного токена нет. Численные пороги учебные, независимо от балла показываются отдельные сигналы.", ""])
    if result["warnings"]:
        lines.extend(["## Уточнения входных данных", "", *result["warnings"], ""])
    return "\n".join(lines)
