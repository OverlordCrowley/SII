"""Проверки требований преподавателя для недель 4–8."""

import json
import subprocess
import sys
import unittest

from project_assistant.assistant import ProjectAssistant
from project_assistant.frames import FrameStore
from project_assistant.knowledge import ROOT, load_knowledge


class Week48Tests(unittest.TestCase):
    def test_forward_and_backward_agree_on_all_examples(self):
        assistant = ProjectAssistant()
        for name in ('ready', 'risky', 'incomplete'):
            profile = json.loads((ROOT / f'examples/{name}.json').read_text(encoding='utf-8'))
            result = assistant.analyze(profile)
            facts = {key: item['value'] for key, item in result['project']['facts'].items()
                     if item['value'] is not None}
            facts.update(result['tokenomics']['facts'])
            forward = assistant.production.run(facts)
            for rule in assistant.knowledge['rules']:
                with self.subTest(example=name, goal=rule['then']):
                    self.assertEqual(forward['facts'].get(rule['then']) is True,
                                     assistant.logical.query(rule['then'], facts) is True)

    def test_multilevel_inheritance_and_local_override(self):
        frames = FrameStore(load_knowledge()['frames'])
        frames.create('example_protocol', 'defi', {'related_node': 'project'})
        description = frames.describe('example_protocol')
        self.assertEqual(description['origins']['quote_currency'], 'crypto_project')
        self.assertEqual(description['origins']['focus'], 'defi')
        original = frames.get('defi', 'focus')
        frames.set('example_protocol', 'focus', 'Локальное значение')
        self.assertEqual(frames.get('defi', 'focus'), original)
        self.assertEqual(frames.describe('example_protocol')['origins']['focus'], 'example_protocol')

    def test_invalid_frame_hierarchy_is_rejected(self):
        for frames in ({'a': {'parent': 'b', 'slots': {}}},
                       {'a': {'parent': 'b', 'slots': {}}, 'b': {'parent': 'a', 'slots': {}}}):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                FrameStore(frames)

    def test_semantic_path_respects_direction(self):
        network = ProjectAssistant().network
        self.assertEqual(network.path('project', 'unlocks'), [
            ['project', 'has', 'token'], ['token', 'has', 'supply'],
            ['supply', 'governed-by', 'vesting'], ['vesting', 'scheduled-by', 'unlocks']])
        self.assertIsNone(network.path('unlocks', 'project'))
        self.assertEqual(len(network.path('unlocks', 'project', undirected=True)), 4)

    def test_week48_cli(self):
        process = subprocess.run([sys.executable, '-m', 'project_assistant', 'week48'],
                                 cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        for week in range(4, 9):
            self.assertIn(f'НЕДЕЛЯ {week}', process.stdout)
        self.assertIn('Требования недель 4–8 прошли', process.stdout)
