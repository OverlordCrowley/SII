"""Воспроизводимый показ требований недель 4–8."""

import json

from .assistant import ProjectAssistant, proof_lines
from .frames import FrameStore
from .knowledge import ROOT
from .logic import evaluate, truth_table


def demonstrate():
    assistant = ProjectAssistant()
    knowledge = assistant.knowledge
    print('НЕДЕЛЯ 4 — предметная область, база знаний и семантическая сеть')
    print('Предметная область: анализ криптопроектов.')
    print('Репозиторий: https://github.com/OverlordCrowley/SII')
    print(f"Критериев: {len(knowledge['criteria'])}; правил: {len(knowledge['rules'])}.")
    print(f'Узлов: {len(assistant.network.nodes)}; связей: {len(assistant.network.edges)}.')
    path = assistant.network.path('project', 'unlocks')
    assert path and len(path) == 4
    for source, relation, target in path:
        print(f'  {source} —{relation}→ {target}')

    print('\nНЕДЕЛЯ 5 — факты, правила и обратный вывод')
    facts = {'audit_available': False, 'admin_controls_known': False}
    print('Факты:', facts)
    proof = assistant.logical.explain('security_gap', facts)
    assert proof['value'] is True and proof['rule'] == 'R16'
    print('Цель security_gap доказана правилом', proof['rule'])

    print('\nНЕДЕЛЯ 6 — AND, OR, NOT и объяснение')
    for expression, values, expected in [
        ('a AND b', {'a': True, 'b': False}, False),
        ('a OR b', {'a': False, 'b': True}, True),
        ('NOT a', {'a': False}, True),
        ('NOT a', {}, None),
    ]:
        actual = evaluate(expression, values)
        assert actual is expected
        print(expression, values, '=>', actual)
    for row in truth_table('a AND (b OR NOT c)'):
        print(row)
    labels = {key: value['label'] for key, value in knowledge['criteria'].items()}
    labels.update({rule['then']: rule['label'] for rule in knowledge['rules']})
    print('\n'.join(proof_lines(proof, labels)))

    print('\nНЕДЕЛЯ 7 — продукционные правила и прямой вывод')
    profile = json.loads((ROOT / 'examples/ready.json').read_text(encoding='utf-8'))
    result = assistant.analyze(profile)
    assert result['status'] == 'research'
    assert any(event['conclusion'] == 'research_ready' for event in result['trace'])
    for event in result['trace']:
        print(f"Шаг {event['step']}: {event['rule']}: {event['condition']} => {event['conclusion']}")
    print('Итог:', result['status_label'])

    print('\nНЕДЕЛЯ 8 — фреймы, объекты, свойства, связи и наследование')
    frames = FrameStore(knowledge['frames'])
    print('Типы фреймов:', ', '.join(frames.frames))
    frames.create('demo_protocol', 'defi', {'name': 'Учебный протокол', 'related_node': 'project'})
    description = frames.describe('demo_protocol')
    print('Связь наследования: crypto_project → defi → demo_protocol')
    for slot, value in description['slots'].items():
        print(f"  {slot}: {value}; источник: {description['origins'][slot]}")
    assert description['origins']['quote_currency'] == 'crypto_project'
    assert description['origins']['focus'] == 'defi'
    frames.set('demo_protocol', 'focus', 'Локальный фокус исследования')
    assert frames.get('defi', 'focus') == knowledge['frames']['defi']['slots']['focus']
    print('Переопределение экземпляра:', frames.get('demo_protocol', 'focus'))
    print('Родитель сохранил значение:', frames.get('defi', 'focus'))
    print('\nТребования недель 4–8 прошли демонстрационную проверку.')
