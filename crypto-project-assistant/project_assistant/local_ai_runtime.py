"""Запуск локальной модели на время работы браузерного приложения."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from .knowledge import ROOT
from .local_ai import CONFIG_PATH, LocalAI, LocalAIError, load_config, save_config

MODEL_FILE = 'Qwen3.5-2B-Q4_K_M.gguf'
STARTUP_TIMEOUT = 120


def find_server(root):
    installed = shutil.which('llama-server')
    if installed:
        return Path(installed)
    folder = root / '.build/local-ai'
    for name in ('llama-server', 'llama-server.exe'):
        candidates = [folder / name, root / name, *sorted(folder.glob('llama-*/' + name), reverse=True)]
        for path in candidates:
            if path.is_file():
                return path
    raise ValueError('Не найден llama-server. Установите llama.cpp и добавьте его в PATH '
                     'или положите llama-server в .build/local-ai. См. docs/LOCAL_AI.md.')


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def wait_ready(client, process, log_path):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise ValueError(f'llama-server завершился с кодом {process.returncode}. Журнал: {log_path}')
        try:
            health = client.request('/health', timeout=2)
        except LocalAIError as error:
            cause = error.__cause__
            if isinstance(cause, HTTPError) and cause.code != 503:
                raise ValueError('На адресе локальной модели отвечает неподходящий сервер. ' + str(error)) from error
        else:
            if health.get('status') == 'ok':
                return client.check()
        time.sleep(0.25)
    raise ValueError(f'Локальная модель не готова за {STARTUP_TIMEOUT} секунд. '
                     f'Проверьте адрес, модель и журнал: {log_path}')


@contextmanager
def local_ai_runtime(config_path=CONFIG_PATH, root=ROOT):
    config = {**load_config(config_path), 'enabled': True}
    client = LocalAI(config)
    address = urlsplit(config['base_url'])
    log_path = root / '.state/local-ai-server.log'
    process = None
    log = None
    try:
        if not port_open(address.hostname, address.port):
            binary = find_server(root)
            model = root / '.build/local-ai' / MODEL_FILE
            if not model.is_file():
                raise ValueError(f'Не найдена модель Qwen: {model}. '
                                 'Поместите GGUF в эту папку. См. docs/LOCAL_AI.md.')
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = log_path.open('wb')
            command = [str(binary), '-m', str(model), '--alias', config['model'] or 'qwen-local',
                       '--host', address.hostname, '--port', str(address.port),
                       '-c', '8192', '-np', '1', '-ngl', '99', '--jinja']
            print('Запускаю Qwen. Ожидаю готовности локальной модели…', flush=True)
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT)
        else:
            print('Проверяю уже запущенную локальную модель…', flush=True)
        ready = wait_ready(client, process, log_path)
        save_config(config, config_path)
        print(f"Локальная ИИ готова: {ready['model']} · {config['base_url']}", flush=True)
        yield ready
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log is not None:
            log.close()
