"""Локальный интерфейс; Python stdlib, без внешних сервисов."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import urlsplit
import webbrowser

from .assistant import ProjectAssistant, markdown_report, parse_profile
from .knowledge import ROOT


def make_server(port=8765):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Порт должен быть целым числом от 0 до 65535')
    assistant = ProjectAssistant()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, kind="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self.respond(200, (ROOT / "web/index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/schema":
                self.respond(200, {**assistant.knowledge, "frames": assistant.frames.frames,
                                   "network": {"nodes": assistant.network.nodes, "edges": assistant.network.edges}})
            elif path.startswith("/api/examples/") and path.rsplit("/", 1)[-1] in {"ready", "risky", "incomplete"}:
                name = path.rsplit("/", 1)[-1]
                self.respond(200, json.loads((ROOT / f"examples/{name}.json").read_text(encoding="utf-8")))
            else:
                self.respond(404, {"error": "Страница не найдена"})

        def do_POST(self):
            if self.path != "/api/analyze":
                self.respond(404, {"error": "Неизвестная команда"})
                return
            local_origins = {None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if self.headers.get("Origin") not in local_origins:
                self.respond(403, {"error": "Запрос должен исходить из локального интерфейса"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 262144:
                    raise ValueError("Размер запроса должен быть от 1 байта до 256 КБ")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Требуется JSON")
                data = parse_profile(self.rfile.read(length).decode("utf-8"))
                result = assistant.analyze(data)
                self.respond(200, {**result, "markdown": markdown_report(result)})
            except (ValueError, UnicodeError) as error:
                self.respond(400, {"error": str(error)})

        def log_message(self, format, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port=8765, open_browser=True):
    server = make_server(port)
    address = f"http://127.0.0.1:{server.server_port}"
    print(f"ИИ-помощник запущен: {address}", flush=True)
    print("Для завершения нажмите Ctrl+C в этом окне.", flush=True)
    if open_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()
