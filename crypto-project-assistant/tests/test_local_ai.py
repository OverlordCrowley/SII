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
            content = {'empty': '', 'thinking': '<think>secret</think>Проверьте vesting.',
                       'unfinished': '<think>unfinished'}.get(self.server.mode, 'Уточните график разблокировок.')
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
        self.assertIn(data['local_ai']['content'], data['markdown'])
        body = self.model.last_request
        self.assertEqual(body['model'], 'qwen-local')
        self.assertFalse(body['stream'])
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(body['max_tokens'], 512)
        self.assertEqual(body['messages'][-1]['content'], 'Почему важен vesting?')
        context = json.loads(body['messages'][1]['content'].split('\n', 1)[1])
        self.assertEqual(len(context['criteria']), 26)
        self.assertEqual(context['score']['display'], '7–48 / 100')
        self.assertEqual(context['criteria'][0]['value'], 'Нет')
        self.assertTrue(any(item['value'] == 'Неизвестно' for item in context['criteria']))

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
        self.assertEqual(LocalAI(self.config).explain(self.result)['content'], 'Проверьте vesting.')
        self.model.mode = 'length'
        result = dict(self.result, local_ai=LocalAI(self.config).explain(self.result))
        self.assertTrue(result['local_ai']['truncated'])
        self.assertIn('лимитом длины', markdown_report(result))

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
