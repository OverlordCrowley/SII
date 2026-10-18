"""Подбор пунктов по вопросу и пояснения по проверенному экспертному результату."""

import json
import re

from .assistant import proof_lines

SELECTION_LIMITS = {"criteria": 3, "rules": 3, "questions": 2}
FACT_TERMS = {
    "product_exists": r"продукт|mainnet|мейннет|приложен|сервис",
    "problem_defined": r"проблем|решаем\w* задач|целев\w* аудитори",
    "users_active": r"пользоват|аудитори|airdrop|раздач",
    "usage_evidence": r"спрос|использован|активност|метрик\w* продукт|продукт\w* метрик",
    "revenue_disclosed": r"доход|выруч|fees|субсид|экономик",
    "competition_analyzed": r"конкурент",
    "team_identified": r"команд|разработчик|основател",
    "roadmap_delivery": r"roadmap|дорожн\w* карт|этап|релиз|обещан",
    "has_token": r"(?:есть|нет|отсутств\w*|собственн\w*|планиру\w*)\s+(?:\w+\s+){0,2}токен|токен\w*\s+(?:нет|есть)|без токен",
    "token_role_defined": r"роль.{0,25}токен|назначен.{0,25}токен|зачем.{0,25}токен|для чего.{0,25}токен|токен.{0,25}нуж",
    "token_value_link": r"связ.{0,80}токен|ценност.{0,25}токен|спрос\w* на токен|продукт.{0,60}стоимост",
    "holder_rights_clear": r"держател|голосован|права.{0,25}токен|выплат",
    "supply_disclosed": r"предложен\w* токен|\bsupply\b|circulating|total supply|max supply",
    "emission_disclosed": r"эмисси|сжиган|burn|mint|выпуск\w* новых токен",
    "vesting_disclosed": r"vesting|вестинг|cliff|клифф|распредел",
    "unlock_schedule_known": r"график.{0,25}(?:разблок|unlock)|календар.{0,25}(?:разблок|unlock)|дат.{0,25}(?:разблок|unlock)",
    "concentration_known": r"концентрац|крупн\w* (?:держател|адрес)|кит\w*|whale",
    "liquidity_known": r"ликвид|liquidity|спред|глубин\w* (?:рынк|стакан)|площадк",
    "valuation_comparable": r"аналог|сопостав|сравн.{0,30}(?:капитал|fdv|оценк)",
    "code_public": r"\bкод\w*|репозитор|github|верифицир",
    "audit_available": r"аудит|audit",
    "admin_controls_known": r"администратор|admin|upgrade|pause|управлен\w* ключ|мультисиг|multisig",
    "incident_history_known": r"инцидент|взлом|hack|эксплойт|exploit",
    "chain_asset_verified": r"сеть|контракт|адрес|нативн\w* актив|идентификац",
    "sources_current": r"источник|первичн|ссылк",
    "metrics_dated": r"датирован|давност|дата.{0,20}(?:снимк|метрик|числ|данн)|на какую дату",
}
METRICS = {
    "price_usd": ("Цена, USD", "input", r"\bцен(?:а|ы|е|у|ой|ам|ах)\b|price", None),
    "market_cap_usd": ("Капитализация, USD", "values", r"капитализац|market.?cap", None),
    "fdv_usd": ("FDV, USD", "values", r"\bfdv\b|полностью разводнен", None),
    "circulating_supply": ("Circulating supply", "input", r"circulating|количеств\w* токен\w* в обращении", None),
    "total_supply": ("Total supply", "input", r"total supply|общее предложен", None),
    "max_supply": ("Max supply", "input", r"max supply|максимальн\w* предложен", None),
    "volume_24h_usd": ("Объём 24ч, USD", "input", r"объем.{0,20}(?:торг|24)|volume|24h", None),
    "next_unlock_tokens": ("Объём ближайшей разблокировки, токены", "input", r"количеств\w* токен.{0,30}(?:unlock|разблок)|next_unlock_tokens", None),
    "volume_to_cap_pct": ("Объём / капитализация", "values", r"объем.{0,30}капитал|volume.{0,30}cap", None),
    "fdv_to_cap": ("FDV / капитализация", "values", r"fdv.{0,40}капитал|отношен.{0,30}fdv|коэффициент.{0,30}fdv", "R22"),
    "circulating_share_pct": ("Доля обращения", "values", r"дол\w* обращени|float|процент.{0,30}обращени", "R23"),
    "unlock_share_pct": ("Unlock / обращение", "values", r"разблок|unlock", "R24"),
    "market_source": ("Источник чисел", "input", r"источник.{0,30}(?:числ|рыноч|цен|данн)|откуда.{0,30}(?:числ|данн)", None),
    "as_of": ("Дата снимка", "input", r"дат\w* снимк|на какую дату.{0,30}(?:числ|цен|данн)", None),
    "unlock_date": ("Дата разблокировки", "input", r"когда.{0,30}(?:разблок|unlock)|дат\w* (?:unlock|разблок)", None),
}
FIELD_TERMS = {"name": r"назван|как называется", "symbol": r"тикер|\bsymbol\b", "network": r"сеть|сет[ьи]",
               "contract": r"контракт|адрес|нативн\w* актив", "description": r"описани"}


def criterion_value(item):
    return "Не применяется" if not item["applicable"] else {True: "Да", False: "Нет", None: "Неизвестно"}[item["value"]]


def question_focus(question, result):
    text = question.lower().replace("ё", "е")
    criteria = [item["id"] for item in result["criteria"] if
                re.search(r"\b" + re.escape(item["id"]) + r"\b", text)
                or item["label"].lower().replace("ё", "е") in text
                or re.search(FACT_TERMS.get(item["id"], r"(?!)"), text)]
    # Общие слова в составном термине не задают ещё одну тему.
    for specific, generic, explicit in [
        ("token_value_link", "usage_evidence", r"метрик|использован"),
        ("concentration_known", "holder_rights_clear", r"прав|выплат|голосован"),
        ("revenue_disclosed", "vesting_disclosed", r"vesting|вестинг|cliff|клифф|распредел\w* токен"),
    ]:
        if specific in criteria and generic in criteria and not re.search(explicit, text):
            criteria.remove(generic)
    if any(key in criteria for key in ("token_value_link", "token_role_defined", "revenue_disclosed")) and not re.search(r"работ|существ|отсутств|нет.{0,20}продукт", text):
        criteria = [key for key in criteria if key != "product_exists"]
    if "sources_current" in criteria and len(criteria) > 1 and not re.search(r"актуаль|устар|свеж|обнов", text):
        criteria.remove("sources_current")
    rules = [f"R{int(match):02d}" for match in re.findall(r"\b[рr]\s*0*(\d{1,4})\b", text)]
    rules = list(dict.fromkeys(rules))
    if rules and re.search(r"код\s+(?:правил|[рr]\s*\d)", text):
        criteria = [key for key in criteria if key != 'code_public']
    known_rules = {item["rule"] for item in result["rule_checks"]}
    metrics = [key for key, (_, _, pattern, _) in METRICS.items() if re.search(pattern, text) or key in text]
    if "unlock_share_pct" in metrics and not re.search(r"объем|дол|процент|значител|размер|рассчит|сколько|\d\s*%", text):
        metrics.remove("unlock_share_pct")
        if "unlock_schedule_known" not in criteria:
            criteria.append("unlock_schedule_known")
    for key, (_, _, _, rule) in METRICS.items():
        if rule and rule in rules and key not in metrics:
            metrics.append(key)
    if "fdv_to_cap" in metrics:
        metrics = [key for key in metrics if key not in ("fdv_usd", "market_cap_usd")]
    if any(key in metrics for key in ("circulating_supply", "total_supply", "max_supply")) and not re.search(r"раскры|извест|определен", text):
        criteria = [key for key in criteria if key != "supply_disclosed"]
    fields = [key for key, pattern in FIELD_TERMS.items() if re.search(pattern, text)]
    topics = [key for key, pattern in {
        "score": r"балл|оценк\w* анализа|проработан|как счита|вес\w* критери",
        "coverage": r"полнот|покрыти|coverage",
        "status": r"статус|критич|blocked|revise|clarify|research",
        "limits": r"доходност|прогноз|купить|покупать|продать|продавать|безопас|будущ\w* цен|цен\w* (?:завтра|выраст|упад)",
    }.items() if re.search(pattern, text)]
    if "limits" in topics and "revenue_disclosed" in criteria and "доходност" in text and not re.search(r"протокол|выруч|fees", text):
        criteria.remove("revenue_disclosed")
    groups = {
        r"токеномик": ['has_token', 'token_role_defined', 'token_value_link', 'holder_rights_clear',
                        'supply_disclosed', 'emission_disclosed', 'vesting_disclosed',
                        'unlock_schedule_known', 'concentration_known'],
        r"техническ\w* безопас|безопасност\w* контракт": ['code_public', 'audit_available', 'admin_controls_known', 'incident_history_known'],
    }
    if not criteria and not rules and not metrics:
        for pattern, keys in groups.items():
            if re.search(pattern, text):
                criteria.extend(keys)
    general = bool(re.search(r"результат анализа|результат разбора|объясни (?:результат|разбор)|кратк\w* разбор|в первую очередь|следующ\w* шаг|главн\w* риск|какие.{0,25}(?:риски|правила)|что.{0,20}(?:выяснить|проверить|уточнить|доработать)|что еще неизвестно", text))
    mode = "general" if general and not (criteria or rules or metrics or fields) else "focused"
    if not (criteria or rules or metrics or fields or topics or general) or set(rules) - known_rules:
        mode = "clarify"
    return {"mode": mode, "criteria": criteria, "rules": rules, "metrics": metrics, "fields": fields, "topics": topics,
            "unknown_rules": sorted(set(rules) - known_rules), "relation": bool(re.search(r"отмен|завис|влия|связ", text))}


def explanation_catalog(result, focus):
    criteria = {item["id"]: item for item in result["criteria"]}
    checks = {item["rule"]: item for item in result["rule_checks"]}
    fired = {item["rule"]: item for item in result["findings"] + result["strengths"]}
    questions = {item["id"]: item for item in result["questions"]}
    required = {key: [] for key in SELECTION_LIMITS}
    if focus["mode"] == "general":
        required["rules"] = [item['rule'] for item in result['findings'] if item['severity'] == 'critical']
        if not required['rules']:
            required['rules'] = list(fired)[:2]
        required["questions"] = list(questions)[:2]
        return {"criteria": criteria, "rules": {key: checks[key] for key in fired}, "questions": questions}, required
    required["criteria"] = list(focus["criteria"])
    required["rules"] = list(focus["rules"])
    for key in focus["metrics"]:
        rule = METRICS[key][3]
        if rule in checks and rule not in required["rules"]:
            required["rules"].append(rule)
    related = []
    for key in focus["criteria"]:
        if key == 'has_token':
            continue
        candidates = [item for item in checks.values() if any(p["id"] == key for p in item["premises"]) and len(item["premises"]) <= 5]
        candidates.sort(key=lambda item: (item["value"] is not True, len(item["premises"]), item["rule"]))
        related.extend(item["rule"] for item in candidates)
        if candidates and not focus["rules"] and candidates[0]["rule"] not in required["rules"]:
            required["rules"].append(candidates[0]["rule"])
    if "status" in focus["topics"] and not focus["rules"]:
        decisive = [item["rule"] for item in result["findings"] if item["severity"] == ("critical" if result["status"] == "blocked" else "high")]
        if result["status"] == "research":
            decisive = [item["rule"] for item in result["strengths"] if item["id"] == "research_ready"]
        required["rules"] = list(dict.fromkeys(required["rules"] + decisive))
    allowed_criteria = set(required["criteria"])
    if not focus['criteria']:
        for key in focus["rules"]:
            allowed_criteria.update(p["id"] for p in checks[key]["premises"] if p["id"] in criteria)
    for key in list(allowed_criteria):
        parent = criteria[key].get("applies_if")
        if parent in criteria:
            allowed_criteria.add(parent)
    required["questions"] = [key for key in questions if key in allowed_criteria]
    allowed_rules = set(required["rules"]) | (set(related) if not focus["rules"] else set())
    return {"criteria": {key: item for key, item in criteria.items() if key in allowed_criteria},
            "rules": {key: item for key, item in checks.items() if key in allowed_rules},
            "questions": {key: item for key, item in questions.items() if key in allowed_criteria}}, required


def selection_limits(required):
    return {key: max(limit, len(required[key])) for key, limit in SELECTION_LIMITS.items()}


def explanation_selection(content, catalog, truncated, required):
    limits = selection_limits(required)
    try:
        if truncated:
            raise ValueError("Выбор обрезан")
        selection = json.loads(content)
        if not isinstance(selection, dict) or set(selection) != set(limits):
            raise ValueError("Неверные поля")
        for key, limit in limits.items():
            values = selection[key]
            if (not isinstance(values, list) or len(values) > limit
                    or any(not isinstance(value, str) for value in values) or len(set(values)) != len(values)):
                raise ValueError("Неверный список")
        original = selection
        selection = {key: list(dict.fromkeys(required[key] + [value for value in original[key] if value in catalog[key]]))[:limits[key]] for key in limits}
        return selection, any(set(selection[key]) != set(original[key]) for key in limits)
    except (ValueError, RecursionError):
        return {key: list(required[key]) for key in limits}, True


def number(value):
    # Не округлять число через порог правила: 2.9999999999999 не должно стать 3.
    return "Неизвестно" if value is None else str(value).removesuffix('.0').replace(".", ",")


def metric_lines(key, result):
    label, section, _, _ = METRICS[key]
    market = result["tokenomics"]
    field = "source" if key == "market_source" else key
    value = market[section].get(field)
    rendered = number(value) if value is None or isinstance(value, (int, float)) else value or "Не указано"
    suffix = "%" if key.endswith("_pct") and value is not None else ""
    lines = [f"{label}: {rendered}{suffix}."]
    inputs = market["input"]
    if key == "unlock_share_pct" and value is not None:
        lines.append(f"Расчёт: {number(inputs['next_unlock_tokens'])} / {number(inputs['circulating_supply'])} × 100% = {number(value)}%.")
        lines.append(f"Дата снимка: {inputs['as_of'] or 'Не указана'}. Дата разблокировки: {inputs['unlock_date'] or 'Не указана'}.")
    elif key == "fdv_to_cap" and value is not None:
        lines.append(f"Расчёт: {number(market['values']['fdv_usd'])} / {number(market['values']['market_cap_usd'])} = {number(value)}.")
    elif key == "circulating_share_pct" and value is not None:
        basis = inputs['total_supply' if inputs['fdv_basis'] == 'total' else 'max_supply']
        lines.append(f"Расчёт: {number(inputs['circulating_supply'])} / {number(basis)} × 100% = {number(value)}%.")
    elif key == "volume_to_cap_pct" and value is not None:
        lines.append(f"Расчёт: {number(inputs['volume_24h_usd'])} / {number(market['values']['market_cap_usd'])} × 100% = {number(value)}%.")
    elif key in ('market_cap_usd', 'fdv_usd') and value is not None:
        supply = 'circulating_supply' if key == 'market_cap_usd' else inputs['fdv_basis'] + '_supply'
        if inputs[key] is not None:
            lines.append('Значение введено в профиле; оно имеет приоритет над вычислением по цене и предложению.')
        else:
            lines.append(f"Расчёт: {number(inputs['price_usd'])} × {number(inputs[supply])} = {number(value)} USD.")
    if value is None and section == 'values':
        lines.append('Показатель не рассчитан: проверьте исходные числа, ненулевой знаменатель и уточнения входных данных ниже.')
    thresholds = {'fdv_to_cap': 'Порог R22: FDV / капитализация ≥ 3.',
                  'circulating_share_pct': 'Порог R23: доля обращения < 25% базы FDV.',
                  'unlock_share_pct': 'Порог R24: unlock ≥ 10% обращения; дата unlock должна быть позже даты снимка.'}
    if key in thresholds:
        lines.append(thresholds[key] + ' Это учебный порог.')
    if result["project"]["facts"].get("has_token", {}).get("value") is False:
        lines.append("Собственного токена нет: сохранённые числа не участвуют в экспертных выводах о токеномике.")
    if inputs['source'] and key != "market_source":
        lines.append("Указанный источник чисел: " + inputs['source'])
    return "\n".join(lines)


def unresolved_premises(item, result, visited=frozenset()):
    """Пояснить производную посылку, которую прямой вывод не установил."""
    checks = {check['conclusion']: check for check in result['rule_checks']}
    lines = []
    for premise in item['premises']:
        child = checks.get(premise['id'])
        if premise['value'] is not None or child is None or child['rule'] in visited:
            continue
        state = 'ложно' if child['value'] is False else 'не установлено'
        lines.append(f"Посылка «{premise['label']}» не выведена: условие {child['rule']} {state} ({child['condition']}).")
        lines.extend(f"  {p['label']}: {criterion_value(p)}." for p in child['premises'])
        lines.extend(unresolved_premises(child, result, visited | {child['rule']}))
    return lines


def grounded_explanation(result, selection, catalog, focus):
    if focus["mode"] == "clarify":
        if focus["unknown_rules"]:
            return "В базе этого проекта нет правил " + ", ".join(focus["unknown_rules"]) + ". Уточните номер правила или назовите критерий."
        return "Не удалось однозначно определить пункт текущего разбора. Уточните вопрос: о каком критерии, правиле, балле или введённом числе идёт речь? Внешние события и котировки этот помощник не проверяет."
    score = result["score"]
    overview = f"Общий балл проработанности анализа: {score['min']}–{score['max']} / 100. Полнота сведений: {score['coverage']}% ({score['known']} из {score['total']} применимых критериев). Статус: {result['status_label']}."
    paragraphs = [overview] if focus["mode"] == "general" else []
    if focus["mode"] == "general" or "score" in focus["topics"]:
        weights = sum(item['weight'] for item in result['criteria'] if item['applicable'])
        positive = sum(item['weight'] for item in result['criteria'] if item['applicable'] and item['value'] is True)
        unknown = sum(item['weight'] for item in result['criteria'] if item['applicable'] and item['value'] is None)
        paragraphs.append(f"Границы общего балла после округления: {positive} / {weights} × 100 = {score['min']}; ({positive} + {unknown}) / {weights} × 100 = {score['max']}. Неизвестное не считается отрицательным ответом. Балл не оценивает доходность или качество монеты.")
    if "coverage" in focus["topics"]:
        paragraphs.append(f"Полнота сведений: {score['known']} / {score['total']} × 100 = {score['coverage']}% после округления.")
    if "limits" in focus["topics"]:
        paragraphs.append("Доходность, будущая цена и безопасность проекта этим разбором не устанавливаются. Доступны только введённые сведения, расчёты и учебные правила.")
    if 'status' in focus['topics']:
        reason = {'blocked': 'Есть сработавшее правило критичного уровня.',
                  'revise': 'Критичных правил нет; есть замечание высокого уровня.',
                  'research': 'Критичных и высоких замечаний нет; выполнены условия готовности исследования R06.',
                  'clarify': 'Критичных и высоких замечаний нет; готовность исследования R06 не установлена.'}[result['status']]
        paragraphs.append('Статус определяется правилами независимо от общего балла. ' + reason)
    detail_start = len(paragraphs)
    for key in selection["criteria"]:
        item = catalog['criteria'][key]
        lines = [f"{item['label']}: {criterion_value(item)}."]
        parent = item.get('applies_if')
        if not item['applicable'] and parent:
            condition = next(c for c in result['criteria'] if c['id'] == parent)
            lines.append(f"Критерий не оценивается, поскольку «{condition['label']}: {criterion_value(condition)}».")
        elif item['value'] is None:
            lines.append("Сведений недостаточно; это не утверждение об отсутствии. Для уточнения: " + item['question'])
        else:
            lines.append("Значение введено в профиле." if item['origin'] == 'input' else "Значение распознано из описания проекта.")
        for field, title in [('evidence', 'Указанное основание'), ('source', 'Указанный источник'), ('date', 'Дата сведений')]:
            if item[field]:
                lines.append(f"{title}: «{item[field]}».")
        if "score" in focus['topics']:
            weight = item['weight'] if item['applicable'] else 0
            lines.append(f"Вес критерия: {weight}. Это вес в общей формуле, а не диапазон общего балла для одного критерия.")
        paragraphs.append("\n".join(lines))
    for key in focus['metrics']:
        paragraphs.append(metric_lines(key, result))
    field_labels = {'name': 'Название', 'symbol': 'Тикер', 'network': 'Сеть', 'contract': 'Контракт / нативный актив', 'description': 'Указанное описание'}
    for key in focus['fields']:
        paragraphs.append(field_labels[key] + ': ' + (result['project'][key] or 'Не указано') + '.')
    labels = {item['id']: item['label'] for item in result['criteria']}
    labels.update(result['computed_labels'])
    labels.update({item['id']: item['label'] for item in result['findings'] + result['strengths']})
    fired = {item['rule']: item for item in result['findings'] + result['strengths']}
    rule_start = len(paragraphs)
    for key in selection['rules']:
        item = catalog['rules'][key]
        state = {True: 'сработало', False: 'не сработало: условие ложно', None: 'не сработало: условие не установлено'}[item['value']]
        lines = [f"Правило {key} {state}.", f"Проверяемый вывод: «{item['label']}».", "Условие: " + item['condition']]
        lines.extend(f"{p['label']}: {criterion_value(p)}." for p in item['premises'])
        if item['value'] is not True:
            lines.extend(unresolved_premises(item, result, frozenset({key})))
        if key in fired and item['value'] is True:
            lines.extend(['Основания экспертного вывода:', *proof_lines(fired[key]['proof'], labels)])
            if item['action']:
                lines.append('Следующий шаг: ' + item['action'])
        if focus['mode'] == 'focused' and focus['relation']:
            absent = [c for c in focus['criteria'] if c not in item['dependencies']]
            if absent:
                lines.append('В условие этого правила не входят: ' + ', '.join(next(c['label'] for c in result['criteria'] if c['id'] == name) for name in absent) + '.')
        paragraphs.append('\n'.join(lines))
    if focus['rules'] and not focus['criteria']:
        paragraphs[detail_start:] = paragraphs[rule_start:] + paragraphs[detail_start:rule_start]
    if selection['questions']:
        paragraphs.append('Что уточнить по вопросу:\n' + '\n'.join(catalog['questions'][key]['question'] for key in selection['questions']))
    if focus['mode'] != 'general':
        paragraphs.append('Контекст проекта. ' + overview)
    critical = [item for item in result['findings'] if item['severity'] == 'critical' and item['rule'] not in selection['rules']]
    if critical:
        paragraphs.append('В общем разборе также есть критичные замечания: ' + '; '.join(item['rule'] + ' — ' + item['label'] for item in critical) + '.')
    if not result['findings'] and focus['mode'] == 'general':
        paragraphs.append('По известным фактам проблемные правила не сработали; это не подтверждение безопасности проекта.')
    if result['warnings'] and (focus['mode'] == 'general' or focus['metrics']):
        paragraphs.append('Уточнения исходных данных:\n' + '\n'.join(result['warnings']))
    return '\n\n'.join(paragraphs)
