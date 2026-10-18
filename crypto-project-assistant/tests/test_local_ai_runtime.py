from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from project_assistant.local_ai import DEFAULTS, LocalAI, load_config, save_config
from project_assistant.local_ai_runtime import MODEL_FILE, local_ai_runtime
from project_assistant.server import serve


MODEL_STUB = '''
import argparse, json, time
from http.server import BaseHTTPRequestHandler, HTTPServer
p = argparse.ArgumentParser()
p.add_argument('--port', type=int)
p.add_argument('--alias')
a = p.parse_args()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = {'status': 'ok'} if self.path == '/health' else {'data': [{'id': a.alias}]}
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self, *args): pass
time.sleep(0.3)
HTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
'''


class LocalAIRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / '.state/local_ai.json'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.config = {**DEFAULTS, 'base_url': f'http://127.0.0.1:{self.port}', 'enabled': False}
        save_config(self.config, self.config_path)

    def stub_start(self, command, **kwargs):
        args = [sys.executable, str(self.stub), '--port', command[command.index('--port') + 1],
                '--alias', command[command.index('--alias') + 1]]
        self.child = self.real_popen(args, **kwargs)
        return self.child

    def prepare_stub(self):
        model = self.root / '.build/local-ai' / MODEL_FILE
        model.parent.mkdir(parents=True)
        model.touch()
        self.stub = self.root / 'model_stub.py'
        self.stub.write_text(MODEL_STUB)
        self.real_popen = subprocess.Popen

    def test_owned_model_is_ready_before_use_and_stops_on_exit(self):
        self.prepare_stub()
        with patch('project_assistant.local_ai_runtime.find_server', return_value=Path(sys.executable)), \
                patch('project_assistant.local_ai_runtime.subprocess.Popen', side_effect=self.stub_start):
            with local_ai_runtime(self.config_path, self.root) as ready:
                self.assertEqual(ready['model'], 'qwen-local')
                self.assertTrue(load_config(self.config_path)['enabled'])
                self.assertIsNone(self.child.poll())
                self.assertTrue(LocalAI(load_config(self.config_path)).check()['ok'])
            self.assertIsNotNone(self.child.poll())

    def test_ready_external_model_is_reused_and_not_terminated(self):
        self.prepare_stub()
        child = self.real_popen([sys.executable, str(self.stub), '--port', str(self.port), '--alias', 'qwen-local'])
        self.addCleanup(lambda: child.wait(timeout=5))
        self.addCleanup(child.terminate)
        from project_assistant.local_ai_runtime import wait_ready
        wait_ready(LocalAI(self.config), child, self.root / 'test.log')
        with patch('project_assistant.local_ai_runtime.subprocess.Popen') as launch:
            with local_ai_runtime(self.config_path, self.root):
                self.assertIsNone(child.poll())
            launch.assert_not_called()
        self.assertIsNone(child.poll())
        save_config({**self.config, 'model': 'missing'}, self.config_path)
        with patch('project_assistant.local_ai_runtime.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'не найдена'):
                with local_ai_runtime(self.config_path, self.root):
                    self.fail('Do not replace an unrelated running model')
            launch.assert_not_called()
        self.assertIsNone(child.poll())

    def test_missing_weights_have_actionable_error_without_launch(self):
        with patch('project_assistant.local_ai_runtime.find_server', return_value=Path(sys.executable)), \
                patch('project_assistant.local_ai_runtime.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'Не найдена модель Qwen'):
                with local_ai_runtime(self.config_path, self.root):
                    self.fail('Application must not start before the model')
            launch.assert_not_called()
        self.assertFalse(load_config(self.config_path)['enabled'])

    def test_failed_or_cancelled_startup_stops_owned_child(self):
        self.prepare_stub()
        for error in (ValueError('startup failed'), KeyboardInterrupt()):
            child = Mock()
            child.poll.return_value = None
            with self.subTest(error=type(error).__name__), \
                    patch('project_assistant.local_ai_runtime.find_server', return_value=Path(sys.executable)), \
                    patch('project_assistant.local_ai_runtime.subprocess.Popen', return_value=child), \
                    patch('project_assistant.local_ai_runtime.wait_ready', side_effect=error):
                with self.assertRaises(type(error)):
                    with local_ai_runtime(self.config_path, self.root):
                        self.fail('Startup must fail')
                child.terminate.assert_called_once()
                child.wait.assert_called_once()

    def test_readiness_timeout_stops_owned_child(self):
        self.prepare_stub()
        child = Mock()
        child.poll.return_value = None
        with patch('project_assistant.local_ai_runtime.find_server', return_value=Path(sys.executable)), \
                patch('project_assistant.local_ai_runtime.subprocess.Popen', return_value=child), \
                patch('project_assistant.local_ai_runtime.STARTUP_TIMEOUT', 0):
            with self.assertRaisesRegex(ValueError, 'Локальная модель не готова'):
                with local_ai_runtime(self.config_path, self.root):
                    self.fail('Do not start the interface without a ready model')
        child.terminate.assert_called_once()
        self.assertFalse(load_config(self.config_path)['enabled'])

    def test_web_server_closes_if_model_startup_fails(self):
        app = Mock()
        with patch('project_assistant.server.make_server', return_value=app), \
                patch('project_assistant.local_ai_runtime.local_ai_runtime', side_effect=ValueError('No model')), \
                patch('project_assistant.server.webbrowser.open') as browser:
            with self.assertRaisesRegex(ValueError, 'No model'):
                serve()
            app.server_close.assert_called_once()
            app.serve_forever.assert_not_called()
            browser.assert_not_called()

    def test_browser_and_web_server_wait_for_model_readiness(self):
        events = []
        @contextmanager
        def runtime():
            events.append('model ready')
            try:
                yield {}
            finally:
                events.append('model stopped')
        app = Mock(server_port=8765)
        app.serve_forever.side_effect = lambda: events.append('web serving')
        with patch('project_assistant.server.make_server', return_value=app), \
                patch('project_assistant.local_ai_runtime.local_ai_runtime', runtime), \
                patch('project_assistant.server.webbrowser.open', side_effect=lambda address: events.append('browser')):
            serve()
        self.assertEqual(events, ['model ready', 'browser', 'web serving', 'model stopped'])
        app.server_close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
