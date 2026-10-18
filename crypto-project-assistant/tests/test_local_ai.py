import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from project_assistant.assistant import ProjectAssistant, markdown_report
from project_assistant.knowledge import ROOT
from project_assistant.explanations import explanation_catalog, question_focus
from project_assistant.local_ai import DEFAULTS, LocalAI, LocalAIError, load_config, save_config, validate_config
from project_assistant.server import make_server


class ModelHandler(BaseHTTPRequestHandler):
    def send(self, status, body, headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.server.mode == 'busy':
            self.send(503, {'error': 'loading'})
        elif self.server.mode == 'redirect':
            self.send(302, {}, {'Location': 'https://example.org/'})
        elif self.path == '/health':
            self.send(200, {'status': 'ok'})
        elif self.path == '/v1/models':
            self.send(200, {'data': [{'id': 'qwen-local'}]})
        else:
            self.send(404, {})

    def do_POST(self):
        self.server.last_request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.server.mode == 'json':
            self.send(200, b'not json')
        elif self.server.mode == 'oversized':
            self.send(200, b' ' * 262145)
        else:
            context = json.loads(self.server.last_request['messages'][1]['content'].split('\n', 1)[1])
            plan = {key: list(context['required'][key]) for key in ('criteria', 'rules', 'questions')}
            for key, field in (('criteria', 'id'), ('rules', 'rule'), ('questions', 'id')):
                if context[key]:
                    plan[key] = list(dict.fromkeys(plan[key] + [context[key][0][field]]))[:context['selection_limits'][key]]
            content = {'empty': '', 'thinking': '<think>secret</think>' + json.dumps(plan),
                       'unfinished': '<think>unfinished', 'contradiction': 'Балл 100. Неизвестное означает Нет.'}.get(self.server.mode, json.dumps(plan))
            self.send(200, {'choices': [{'message': {'content': content},
                                       'finish_reason': 'length' if self.server.mode == 'length' else 'stop'}]})

    def log_message(self, *args):
        pass


class LocalAITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.config_path = Path(cls.temp.name) / 'ai.json'
        cls.model = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
        cls.model_thread = threading.Thread(target=cls.model.serve_forever, daemon=True)
        cls.model_thread.start()
        cls.app = make_server(0, cls.config_path)
        cls.app_thread = threading.Thread(target=cls.app.serve_forever, daemon=True)
        cls.app_thread.start()
        cls.url = f'http://127.0.0.1:{cls.app.server_port}'
        cls.config = {**DEFAULTS, 'enabled': True, 'base_url': f'http://127.0.0.1:{cls.model.server_port}'}
        cls.profile = json.loads((ROOT / 'examples/risky.json').read_text(encoding='utf-8'))
        cls.result = ProjectAssistant().analyze(cls.profile)

    @classmethod
    def tearDownClass(cls):
        for server, thread in ((cls.app, cls.app_thread), (cls.model, cls.model_thread)):
            server.shutdown()
            server.server_close()
            thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.model.mode = 'ok'
        self.model.last_request = None
        save_config(self.config, self.config_path)

    def api(self, path, body=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        data = None if body is None else json.dumps(body).encode()
        with urlopen(Request(self.url + path, data=data, headers=headers), timeout=10) as response:
            return json.load(response)

    def assert_api_error(self, code, path, body, origin=None):
        with self.assertRaises(HTTPError) as caught:
            self.api(path, body, origin)
        with caught.exception as response:
            self.assertEqual(response.code, code)
            return json.load(response)['error']

    def test_config_is_local_and_validates_types(self):
        for raw in [{'base_url': url} for url in ('https://127.0.0.1:8080', 'http://example.com',
                'http://127.0.0.1:0', 'http://localhost:70000', 'http://127.0.0.1@evil.com',
                'http://user:pass@localhost', 'http://localhost/path', 'http://localhost?x=1',
                'http://localhost#x', 'http://local\nhost:8080')]+[
                {'enabled': 1}, {'timeout': True}, {'timeout': 4}, {'timeout': 301},
                {'model': 1}, {'model': 'bad\nmodel'}, {'unknown': True}]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_config(raw)
        self.assertEqual(validate_config({'base_url': 'http://localhost:8080/v1/'})['base_url'], 'http://127.0.0.1:8080')
        self.assertEqual(validate_config({'base_url': 'http://[::1]:8080'})['base_url'], 'http://[::1]:8080')

    def test_config_roundtrip_and_corruption_recovery(self):
        self.assertEqual(self.api('/api/local-ai/config')['config'], self.config)
        self.config_path.write_text('broken', encoding='utf-8')
        data = self.api('/api/local-ai/config')
        self.assertEqual(data['config'], DEFAULTS)
        self.assertIn('Повреждены', data['error'])
        self.api('/api/local-ai/config', self.config)
        self.assertEqual(load_config(self.config_path), self.config)
        self.config_path.unlink()
        self.assertEqual(load_config(self.config_path), DEFAULTS)

    def test_auto_model_and_v1_url(self):
        config = {**self.config, 'base_url': self.config['base_url'] + '/v1'}
        self.assertEqual(LocalAI(config).check()['model'], 'qwen-local')
        self.assertEqual(self.api('/api/local-ai/check', config)['model'], 'qwen-local')
        with self.assertRaisesRegex(LocalAIError, 'не найдена'):
            LocalAI({**config, 'model': 'missing'}).check()

    def test_generation_receives_bounded_expert_context(self):
        data = self.api('/api/local-ai/explain', {'project': self.profile, 'question': 'Почему важен vesting?'})
        self.assertEqual(data['score'], self.result['score'])
        self.assertEqual(data['status'], self.result['status'])
        self.assertEqual(data['findings'], self.result['findings'])
        self.assertIn('Пояснение локальной ИИ', data['markdown'])
        self.assertIn(data['local_ai']['content'].splitlines()[0], data['markdown'])
        body = self.model.last_request
        self.assertEqual(body['model'], 'qwen-local')
        self.assertFalse(body['stream'])
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(body['reasoning_effort'], 'none')
        self.assertEqual(body['temperature'], 0)
        schema = body['response_format']['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['rules']['items']['enum'], ['R03', 'R13'])
        self.assertEqual(body['max_tokens'], 512)
        self.assertEqual(body['messages'][-1]['content'], 'Почему важен vesting?')
        context = json.loads(body['messages'][1]['content'].split('\n', 1)[1])
        self.assertEqual({item['id'] for item in context['criteria']}, {'has_token', 'vesting_disclosed'})
        self.assertEqual(context['required']['criteria'], ['vesting_disclosed'])
        self.assertEqual(context['required']['rules'], ['R13'])
        self.assertEqual(context['score']['display'], '7–48 / 100')
        self.assertEqual(next(item['value'] for item in context['criteria'] if item['id'] == 'vesting_disclosed'), 'Нет')

    def explain_reply(self, content, result=None, finish_reason='stop', question=''):
        client = LocalAI(self.config)
        response = {'choices': [{'message': {'content': content}, 'finish_reason': finish_reason}]}
        with patch.object(client, 'check', return_value={'model': 'qwen-local'}), patch.object(client, 'request', return_value=response):
            return client.explain(result or self.result, question)

    def test_narrow_question_replaces_valid_but_irrelevant_selection(self):
        unrelated = {'criteria': ['product_exists'], 'rules': ['R07'], 'questions': ['problem_defined']}
        for content in (json.dumps(unrelated), '{', json.dumps({'criteria': [], 'rules': [], 'questions': []})):
            with self.subTest(content=content):
                answer = self.explain_reply(content, question='Почему vesting не раскрыт?')
                self.assertIn('vesting_disclosed', answer['selection']['criteria'])
                self.assertIn('R13', answer['selection']['rules'])
                self.assertNotIn('product_exists', answer['selection']['criteria'])
                self.assertNotIn('R07', answer['selection']['rules'])
                self.assertNotIn('problem_defined', answer['selection']['questions'])
                self.assertTrue(answer['fallback'])
                self.assertNotIn('Правило R10:', answer['content'])

    def test_question_about_two_topics_keeps_both(self):
        plan = {'criteria': ['audit_available'], 'rules': ['R16'], 'questions': []}
        answer = self.explain_reply(json.dumps(plan), question='Что известно об аудите и vesting?')
        self.assertIn('audit_available', answer['selection']['criteria'])
        self.assertIn('vesting_disclosed', answer['selection']['criteria'])
        self.assertIn('R13', answer['selection']['rules'])
        complete = {'criteria': ['audit_available', 'vesting_disclosed'], 'rules': ['R16', 'R13'], 'questions': []}
        answer = self.explain_reply(json.dumps(complete), question='Что известно об аудите и vesting?')
        self.assertFalse(answer['fallback'])

    def test_question_about_a_rule_that_did_not_fire_has_its_condition(self):
        result = ProjectAssistant().analyze(json.loads((ROOT / 'examples/ready.json').read_text()))
        answer = self.explain_reply('{', result, question='Почему R13 не сработало?')
        self.assertEqual(answer['selection']['rules'], ['R13'])
        self.assertTrue(answer['content'].startswith('Правило R13 не сработало'))
        self.assertIn('Проверяемый вывод: «Распределение или график unlocks не раскрыты»', answer['content'])
        self.assertIn('Распределение и vesting раскрыты: Да', answer['content'])
        self.assertIn('График разблокировок известен: Да', answer['content'])

    def test_numeric_question_has_exact_unlock_formula_and_inputs(self):
        answer = self.explain_reply('{', question='Как рассчитаны 20% ближайшей разблокировки?')
        self.assertIn('unlock_share_pct', answer['focus']['metrics'])
        self.assertIn('Unlock / обращение: 20%', answer['content'])
        self.assertIn('2000000 / 10000000 × 100% = 20%', answer['content'])
        self.assertIn('R24', answer['selection']['rules'])
        self.assertNotIn('roadmap_delivery', answer['selection']['criteria'])

    def test_unknown_or_ambiguous_question_requests_clarification(self):
        client = LocalAI(self.config)
        for question in ('Какая погода завтра в Алматы?', 'Почему?', 'Почему R99 не сработало?'):
            with self.subTest(question=question), patch.object(client, 'check', return_value={'model': 'qwen-local'}), patch.object(client, 'request') as generate:
                answer = client.explain(self.result, question)
                self.assertTrue(answer['clarification'])
                self.assertIn('Уточните', answer['content'])
                self.assertNotIn('Правило R10:', answer['content'])
                generate.assert_not_called()

    def test_tokenless_vesting_question_stays_on_vesting(self):
        result = ProjectAssistant().analyze({'name': 'Test', 'facts': {'has_token': False, 'vesting_disclosed': False}})
        plan = {'criteria': ['token_role_defined'], 'rules': [], 'questions': ['product_exists']}
        answer = self.explain_reply(json.dumps(plan), result, question='Почему vesting отрицательный, если токена нет?')
        self.assertIn('vesting_disclosed', answer['selection']['criteria'])
        self.assertNotIn('token_role_defined', answer['selection']['criteria'])
        self.assertIn('Распределение и vesting раскрыты: Не применяется', answer['content'])
        self.assertIn('У проекта есть или планируется токен: Нет', answer['content'])

    def test_common_topic_terms_select_the_requested_fact(self):
        topics = {'product_exists': 'Есть ли работающий продукт?', 'problem_defined': 'Какая проблема решается?',
                  'users_active': 'Что известно об активных пользователях?', 'usage_evidence': 'Есть ли метрики использования?',
                  'revenue_disclosed': 'Раскрыта ли выручка?', 'competition_analyzed': 'Какие конкуренты?',
                  'team_identified': 'Что известно о команде?', 'roadmap_delivery': 'Что известно о roadmap?',
                  'has_token': 'Есть ли собственный токен?', 'token_role_defined': 'Зачем нужен токен?',
                  'token_value_link': 'Есть ли связь спроса на токен с продуктом?', 'holder_rights_clear': 'Какие права держателей?',
                  'supply_disclosed': 'Раскрыто ли предложение токенов?', 'emission_disclosed': 'Как проверена эмиссия?',
                  'vesting_disclosed': 'Что известно о vesting?', 'unlock_schedule_known': 'Что известно о графике unlock?',
                  'concentration_known': 'Что известно о концентрации?', 'liquidity_known': 'Как проверена ликвидность?',
                  'valuation_comparable': 'Какие аналоги для оценки?', 'code_public': 'Открыт ли исходный код?',
                  'audit_available': 'Есть ли аудит?', 'admin_controls_known': 'Как проверены admin ключи?',
                  'incident_history_known': 'Что известно об инцидентах?', 'chain_asset_verified': 'Как проверен контракт?',
                  'sources_current': 'Актуальны ли источники?', 'metrics_dated': 'На какую дату метрики?'}
        self.assertEqual(set(topics), {c['id'] for c in self.result['criteria']})
        for key, question in topics.items():
            with self.subTest(question=question):
                focus = question_focus(question, self.result)
                self.assertIn(key, focus['criteria'])
                catalog, required = explanation_catalog(self.result, focus)
                answer = self.explain_reply('{', question=question)
                self.assertIn(key, answer['selection']['criteria'])
                self.assertTrue(set(answer['selection']['criteria']) <= set(catalog['criteria']))
                self.assertTrue(set(required['rules']) <= set(answer['selection']['rules']))

    def test_multiple_explicit_topics_are_not_cut_to_three(self):
        question = 'Что известно об аудите, vesting, ликвидности и команде?'
        answer = self.explain_reply('{', question=question)
        self.assertEqual(set(answer['selection']['criteria']),
                         {'audit_available', 'vesting_disclosed', 'liquidity_known', 'team_identified'})
        self.assertIn('liquidity_known', answer['selection']['questions'])
        self.assertIn('team_identified', answer['selection']['questions'])

    def test_numeric_rule_references_show_formula_and_real_threshold(self):
        for rule, formula in [('R22', '100000000 / 10000000 = 10'),
                              ('R23', '10000000 / 100000000 × 100% = 10%'),
                              ('R24', '2000000 / 10000000 × 100% = 20%')]:
            with self.subTest(rule=rule):
                answer = self.explain_reply('{', question='Почему ' + rule + ' сработало?')
                self.assertEqual(answer['selection']['rules'], [rule])
                self.assertIn(formula, answer['content'])
                self.assertIn('Порог ' + rule, answer['content'])
        result = ProjectAssistant().analyze({'name': 'No numbers', 'facts': {'has_token': True}})
        answer = self.explain_reply('{', result, question='Почему R24 не сработало?')
        self.assertIn('Unlock / обращение: Неизвестно', answer['content'])
        self.assertIn('Правило R24 не сработало: условие не установлено', answer['content'])
        self.assertNotIn('Правило R24 не сработало: условие ложно', answer['content'])

    def test_metric_display_does_not_round_across_rule_thresholds(self):
        cases = [('R22', {'market_cap_usd': 1, 'fdv_usd': 2.9999999999999}, False, 'FDV / капитализация: 3.'),
                 ('R23', {'circulating_supply': 24.99999999999, 'total_supply': 100}, True, 'Доля обращения: 25%'),
                 ('R24', {'circulating_supply': 100, 'next_unlock_tokens': 9.999999999999,
                          'as_of': '2026-10-04', 'unlock_date': '2026-10-20'}, False, 'Unlock / обращение: 10%')]
        for rule, market, value, incorrect in cases:
            with self.subTest(rule=rule):
                result = ProjectAssistant().analyze({'name': 'Threshold', 'facts': {'has_token': True}, 'market': market})
                check = next(c for c in result['rule_checks'] if c['rule'] == rule)
                self.assertIs(check['value'], value)
                answer = self.explain_reply('{', result, question='Почему ' + rule + ' сработало?')
                self.assertNotIn(incorrect, answer['content'])

    def test_rule_relations_and_unresolved_derived_premises_are_explained(self):
        answer = self.explain_reply('{', question='Отсутствие продукта отменяет R13?')
        self.assertEqual(answer['selection']['rules'], ['R13'])
        self.assertIn('В условие этого правила не входят: Есть работающий продукт', answer['content'])
        profile = json.loads((ROOT / 'examples/ready.json').read_text())
        profile['facts']['usage_evidence'] = False
        result = ProjectAssistant().analyze(profile)
        answer = self.explain_reply('{', result, question='Как спрос влияет на R06?')
        self.assertIn('условие R01 ложно', answer['content'])
        self.assertIn('Спрос подтверждён метриками: Нет', answer['content'])
        self.assertNotIn('В условие этого правила не входят: Спрос подтверждён метриками', answer['content'])
        unrelated_premises = {'criteria': ['team_identified', 'roadmap_delivery'], 'rules': ['R06'], 'questions': []}
        answer = self.explain_reply(json.dumps(unrelated_premises), result, question='Как спрос влияет на R06?')
        self.assertEqual(answer['selection']['criteria'], ['usage_evidence'])
        self.assertTrue(answer['fallback'])

    def test_topic_collisions_do_not_add_unrelated_price_or_code(self):
        answer = self.explain_reply('{', question='Как ценность токена связана с продуктом?')
        self.assertNotIn('price_usd', answer['focus']['metrics'])
        answer = self.explain_reply('{', question='Покажи код правила R13')
        self.assertNotIn('code_public', answer['selection']['criteria'])
        self.assertEqual(answer['selection']['rules'], ['R13'])

    def test_profile_provenance_and_score_question_do_not_invent_facts(self):
        profile = {'name': 'Test', 'facts': {'vesting_disclosed': {'value': False, 'evidence': 'Документ отсутствует',
                   'source': 'https://example.org/vesting', 'date': '2026-10-01'}, 'has_token': True}}
        result = ProjectAssistant().analyze(profile)
        answer = self.explain_reply('{', result, question='Откуда сведения про vesting?')
        self.assertIn('Указанное основание: «Документ отсутствует»', answer['content'])
        self.assertIn('Указанный источник: «https://example.org/vesting»', answer['content'])
        self.assertIn('Дата сведений: «2026-10-01»', answer['content'])
        answer = self.explain_reply('{', result, question='Как рассчитаны общий балл и полнота?')
        self.assertIn('Границы общего балла после округления', answer['content'])
        self.assertIn('Полнота сведений: 2 / 26', answer['content'])
        self.assertFalse(answer['model_selection'])
        self.assertEqual(answer['selection']['rules'], [])

    def test_explanation_uses_exact_score_status_and_rule_proof(self):
        before = json.dumps(self.result, sort_keys=True)
        plan = {'criteria': ['product_exists'], 'rules': ['R10', 'R16', 'R13'],
                'questions': ['problem_defined', 'token_role_defined']}
        answer = self.explain_reply(json.dumps(plan))
        self.assertTrue(answer['grounded'])
        self.assertFalse(answer['fallback'])
        self.assertIn('Общий балл проработанности анализа: 7–48 / 100', answer['content'])
        self.assertIn('Статус: ' + self.result['status_label'], answer['content'])
        self.assertIn('Есть работающий продукт: Нет', answer['content'])
        rule = next(item for item in self.result['findings'] if item['rule'] == 'R13')
        self.assertIn(rule['label'], answer['content'])
        self.assertIn(rule['action'], answer['content'])
        self.assertIn('Распределение и vesting раскрыты: НЕТ', answer['content'])
        self.assertEqual(json.dumps(self.result, sort_keys=True), before)

    def test_untrusted_or_invalid_reply_cannot_override_expert_result(self):
        plan = {'criteria': [], 'rules': [], 'questions': []}
        invalid = ['Общий балл 100. Проект безопасен. R99 отменяет R13.',
                   json.dumps({**plan, 'score': 100}), json.dumps({**plan, 'answer': 'Продукт существует'}),
                   json.dumps({**plan, 'rules': ['R99']}), json.dumps({**plan, 'rules': ['R01']}),
                   json.dumps({**plan, 'criteria': ['missing']}), json.dumps({**plan, 'criteria': ['product_exists'] * 4}),
                   json.dumps({**plan, 'criteria': ['product_exists'] * 2}), json.dumps({**plan, 'criteria': [True]}),
                   json.dumps({**plan, 'questions': ['product_exists']}), json.dumps({'criteria': []}),
                   '[]', 'null', '{', '{"criteria": [[[]]], "rules": [], "questions": []}']
        for content in invalid:
            with self.subTest(content=content):
                answer = self.explain_reply(content)
                self.assertTrue(answer['grounded'])
                self.assertTrue(answer['fallback'])
                self.assertIn('7–48 / 100', answer['content'])
                self.assertIn('Статус: ' + self.result['status_label'], answer['content'])
                self.assertNotIn('Проект безопасен', answer['content'])
                self.assertNotIn('R99', answer['content'])

    def test_unknown_and_inapplicable_criteria_are_preserved(self):
        result = ProjectAssistant().analyze({'name': 'Test', 'facts': {'has_token': False, 'vesting_disclosed': False}})
        plan = {'criteria': ['product_exists', 'vesting_disclosed'], 'rules': [], 'questions': []}
        answer = self.explain_reply(json.dumps(plan), result)
        self.assertTrue(answer['fallback'])
        self.assertIn('Есть работающий продукт: Неизвестно', answer['content'])
        self.assertIn('Распределение и vesting раскрыты: Не применяется', answer['content'])
        self.assertNotIn('Распределение и vesting раскрыты: Нет', answer['content'])

    def test_critical_rule_is_shown_even_when_model_selects_only_strengths(self):
        profile = json.loads((ROOT / 'examples/ready.json').read_text())
        profile['facts']['chain_asset_verified'] = False
        result = ProjectAssistant().analyze(profile)
        self.assertGreater(result['score']['min'], 90)
        self.assertEqual(result['status'], 'blocked')
        plan = {'criteria': [], 'rules': ['R01'], 'questions': []}
        answer = self.explain_reply(json.dumps(plan), result)
        self.assertTrue(answer['fallback'])
        self.assertIn('Статус: ' + result['status_label'], answer['content'])
        self.assertIn('Сеть и актив не сверены', answer['content'])
        self.assertIn('Сеть и актив однозначно определены: НЕТ', answer['content'])

    def test_disabled_and_invalid_input_never_generate(self):
        save_config({**self.config, 'enabled': False}, self.config_path)
        self.assertIn('Включите', self.assert_api_error(400, '/api/local-ai/explain', {'project': self.profile}))
        self.assertIsNone(self.model.last_request)
        save_config(self.config, self.config_path)
        for body in ({'project': self.profile, 'question': 1}, {'project': self.profile, 'question': 'x' * 1201},
                     {'project': {'facts': {'has_token': 1}}}, {'project': self.profile, 'score': 100}):
            with self.subTest(body=body):
                self.assert_api_error(400, '/api/local-ai/explain', body)
        self.assertIsNone(self.model.last_request)

    def test_loading_and_redirects_fail_without_breaking_analysis(self):
        for mode, message in [('busy', 'загружается'), ('redirect', 'Перенаправления')]:
            self.model.mode = mode
            self.assertIn(message, self.assert_api_error(503, '/api/local-ai/explain', {'project': self.profile}))
        self.assertEqual(self.api('/api/analyze', self.profile)['status'], self.result['status'])

    def test_response_validation_and_thinking_cleanup(self):
        for mode in ('json', 'empty', 'unfinished', 'oversized'):
            self.model.mode = mode
            with self.subTest(mode=mode), self.assertRaises(LocalAIError):
                LocalAI(self.config).explain(self.result)
        self.model.mode = 'thinking'
        answer = LocalAI(self.config).explain(self.result)
        self.assertTrue(answer['grounded'])
        self.assertFalse(answer['fallback'])
        self.assertNotIn('secret', answer['content'])
        self.model.mode = 'length'
        result = dict(self.result, local_ai=LocalAI(self.config).explain(self.result))
        self.assertTrue(result['local_ai']['truncated'])
        self.assertTrue(result['local_ai']['fallback'])
        self.assertIn('лимитом длины', markdown_report(result))

    def test_api_and_markdown_use_grounded_fallback_for_contradictory_model_text(self):
        self.model.mode = 'contradiction'
        result = self.api('/api/local-ai/explain', {'project': self.profile})
        self.assertTrue(result['local_ai']['grounded'])
        self.assertTrue(result['local_ai']['fallback'])
        self.assertEqual(result['score'], self.result['score'])
        self.assertEqual(result['status'], self.result['status'])
        self.assertEqual(result['findings'], self.result['findings'])
        self.assertIn('Общий балл проработанности анализа: 7–48 / 100', result['markdown'])
        self.assertNotIn('Неизвестное означает Нет', result['markdown'])

    def test_timeout_and_disconnected_socket_have_friendly_errors(self):
        client = LocalAI(self.config)
        for error, message in ((TimeoutError(), 'таймаут'), (ConnectionResetError(), 'прервано')):
            with self.subTest(error=error), patch.object(client.opener, 'open', side_effect=error):
                with self.assertRaisesRegex(LocalAIError, message):
                    client.check()

    def test_new_routes_keep_origin_and_configuration_validation(self):
        for path, body in (('/api/local-ai/config', self.config), ('/api/local-ai/check', self.config),
                           ('/api/local-ai/explain', {'project': self.profile})):
            self.assert_api_error(403, path, body, 'https://example.org')
        self.assert_api_error(400, '/api/local-ai/config', {'base_url': 'http://example.org'})
        self.assertEqual(load_config(self.config_path), self.config)

    def test_cli_can_configure_check_and_explain(self):
        def command(*args):
            return subprocess.run([sys.executable, '-m', 'project_assistant', *args], cwd=ROOT,
                                  capture_output=True, text=True, encoding='utf-8', timeout=15)
        path = str(Path(self.temp.name) / 'cli-ai.json')
        result = command('local-ai', 'configure', '--config-file', path, '--url', self.config['base_url'], '--enabled')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['enabled'])
        result = command('local-ai', 'check', '--config-file', path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['model'], 'qwen-local')
        result = command('analyze', 'examples/risky.json', '--json', '--local-ai', '--ai-config', path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('local_ai', json.loads(result.stdout))
        result = command('ask', 'BTC', '--local-ai', '--ai-config', path, '--ai-question', 'Что ещё неизвестно?')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Bitcoin', result.stdout)
        self.assertIn('Что ещё неизвестно?', result.stdout)
        result = command('ask', 'BTC', '--ai-question', 'test')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
