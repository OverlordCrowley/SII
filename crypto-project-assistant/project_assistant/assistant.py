"""Анализ описания, объяснимый вывод и вопросы при неполных данных."""

from datetime import datetime, timezone
import json
from math import isfinite
import re

from .assets import load_aliases, resolve_identity
from .expert import ExpertSystem
from .knowledge import ROOT
from .ml import ModelStore
from .tokenomics import NUMBERS, calculate


def parse_profile(raw):
    def invalid_number(value):
        raise ValueError(f'JSON содержит недопустимое число {value}. Используйте конечное число или null.')

    try:
        profile = json.loads(raw, parse_constant=invalid_number)
    except RecursionError as error:
        raise ValueError('JSON содержит слишком много вложенных объектов или массивов') from error
    # A fixed limit also applies to Python decoders that accept deeper JSON.
    pending = [(profile, 1)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, float) and not isfinite(value):
            invalid_number(value)
        if isinstance(value, (dict, list)):
            if depth > 64:
                raise ValueError('JSON содержит слишком много вложенных объектов или массивов')
            children = value.values() if isinstance(value, dict) else value
            pending.extend((child, depth + 1) for child in children)
    return profile


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
    def __init__(self, state_dir=ROOT / ".state", models=True):
        self.expert = ExpertSystem(state_dir)
        self.knowledge = self.expert.knowledge
        self.asset_aliases = load_aliases()
        self.criteria = self.knowledge["criteria"]
        self.logical = self.expert.logical
        self.production = self.expert.production
        self.frames = self.expert.frames
        self.network = self.expert.network
        self.models = ModelStore(self.knowledge) if models else None

    def profile(self, data):
        if not isinstance(data, dict):
            raise ValueError("Описание проекта должно быть объектом JSON")
        name = text(data.get("name", ""), "Название", 200)
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
                raise ValueError(f"Критерий «{self.criteria[key]['label']}» ({key}): допустимы true, false или null")
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
        identity = {key: text(data.get(key, ""), label, 250)
                    for key, label in (("symbol", "Тикер"), ("network", "Сеть"), ("contract", "Контракт / нативный актив"))}
        name, identity['symbol'] = resolve_identity(name, identity['symbol'], description, self.asset_aliases)
        tokenomics = calculate(data.get("market", {}))
        return {"name": name, "description": description, "type": kind, "facts": claims, **identity, "market": tokenomics["input"]}, warnings

    def analyze(self, data):
        profile, warnings = self.profile(data)
        facts = {key: item["value"] for key, item in profile["facts"].items() if item["value"] is not None}
        facts = {key: value for key, value in facts.items() if facts.get(self.criteria[key].get('applies_if')) is not False}
        tokenomics = calculate(profile["market"])
        warnings.extend(tokenomics["warnings"])
        if facts.get('has_token') is not False:
            facts.update(tokenomics["facts"])
        elif any(profile['market'][key] is not None for key in NUMBERS):
            warnings.append('Указано отсутствие собственного токена: токеномика сохранена в профиле, но не участвует в выводах.')
        result = self.expert.infer(facts)
        findings, strengths = [], []
        for rule in self.knowledge["rules"]:
            goal = rule["then"]
            if result["facts"].get(goal) is not True:
                continue
            proof = result["proofs"][goal]
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
        status = result["status"]
        label = {"blocked": "Сначала проверьте критичные сведения", "revise": "Исследование требует дополнения",
                 "research": "Есть основа для углублённого сравнения", "clarify": "Нужно уточнить проект"}[status]
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
        recognition = self.models.recognize(result["initial"], status) if self.models else {"available": False, "predictions": [], "scope": "", "reason": "Модели отключены для проверки правил."}
        return {"project": profile, "generated_at": datetime.now(timezone.utc).isoformat(), "status": status, "status_label": label,
                "summary": summary, "score": score, "findings": findings, "strengths": strengths, "questions": questions,
                "next_actions": actions, "criteria": assessments, "warnings": warnings, "trace": result["trace"],
                "frame": self.frames.describe(profile["type"]), "related": self.network.related("project", depth=2), "tokenomics": tokenomics,
                "computed_labels": self.knowledge["computed"], "recognition": recognition,
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
    def clean(value):
        return re.sub(r'([\\`*_\[\]<>|])', r'\\\1', ' '.join(str(value).splitlines()))

    def quoted(value):
        return '\n'.join('> ' + clean(line) for line in str(value).splitlines())
    score = result["score"]
    lines = [f"# Анализ проекта: {clean(result['project']['name'])}", "", f"Дата анализа (UTC): {result['generated_at']}", "",
             clean(result["summary"]), "", f"Проработанность анализа: **{score['min']}–{score['max']} / 100**. Полнота сведений: **{score['coverage']}%**.", "",
             result["limits"], "", "## Идентификация проекта", "",
             f"Тип: {clean(result['project']['type'])}. Тикер: {clean(result['project']['symbol']) or 'не указан'}.",
             f"Сеть: {clean(result['project']['network']) or 'не указана'}. Контракт / нативный актив: {clean(result['project']['contract']) or 'не указан'}.",
             "", "## Описание", "", quoted(result["project"]["description"]) or "Описание не введено.", "", "## Рекомендации", ""]
    lines.extend(f"{index}. {action}" for index, action in enumerate(result["next_actions"], 1))
    if not result["next_actions"]:
        lines.append("Сравните проект с аналогами на одну дату и проверьте введённые основания по первичным источникам.")
    numbers = result["tokenomics"]
    lines.extend(["", "## Числа и токеномика", "", f"Валюта: USD. Дата снимка: {numbers['input']['as_of'] or 'не указана'}. Источник: {clean(numbers['input']['source']) or 'не указан'}.", ""])
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
    recognition = result["recognition"]
    lines.extend(["", "## Обученные модели", "", recognition["scope"], ""])
    if not recognition["available"]:
        lines.append(recognition["reason"])
    for item in recognition["predictions"]:
        agreement = "совпадает с экспертным выводом" if item["agrees_with_expert"] else "расходится с экспертным выводом; используйте объяснение правил"
        lines.append(f"- {item['model']}: {item['label']} ({agreement}). Точность на синтетической тестовой выборке: {item['test_accuracy']:.1%}, macro-F1: {item['test_macro_f1']:.3f}.")
    lines.extend(["", "## Метод оценки", "", "У каждого применимого критерия есть фиксированный вес в базе знаний. Нижняя граница = сумма весов известных положительных критериев / сумма применимых весов. Верхняя граница допускает положительные ответы по неизвестным критериям. Токеномика исключается из оценки при явном ответе, что собственного токена нет. Численные пороги учебные, независимо от балла показываются отдельные сигналы.", ""])
    if result.get("local_ai"):
        ai = result["local_ai"]
        lines.extend(["", "## Пояснение локальной ИИ", "", f"Модель: {clean(ai['model'])}. Сервер: {clean(ai['base_url'])}.",
                      f"Вопрос: {clean(ai['question'])}", "", quoted(ai["content"]), "",
                      "Текст создан локальной языковой моделью и может содержать ошибки. Балл, статус и доказательства выше рассчитаны экспертными правилами."])
        if ai["truncated"]:
            lines.append("Ответ ограничен лимитом длины генерации.")
    if result["warnings"]:
        lines.extend(["## Уточнения входных данных", "", *result["warnings"], ""])
    return "\n".join(lines)
