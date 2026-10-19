"""Этап 2: минимальный Mock API для обучения HTTP Request в n8n.

Реализация POST /requests, служебного API и имитации сбоев запланирована
на этап 3. Этот сервер не является полной реализацией API_CONTRACT.md.
Никаких внешних зависимостей.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import json
import re

USERS = {
    451: {"id": 451, "name": "Иван Иванов", "department": "IT", "email": "ivan@example.com"},
    452: {"id": 452, "name": "Мария Смирнова", "department": "Finance", "email": "maria@example.com"},
    453: {"id": 453, "name": "Петр Орлов", "department": "Security", "email": "petr@example.com"},
}


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/health":
            self.send_json(200, {"status": "ok", "stage": 2})
            return

        match = re.fullmatch(r"/users/(\d+)", path)
        if match:
            user = USERS.get(int(match.group(1)))
            if user is None:
                self.send_json(404, {"error": {"code": "USER_NOT_FOUND", "message": "Пользователь не найден"}})
            else:
                self.send_json(200, user)
            return
        self.send_json(404, {"error": {"code": "NOT_IMPLEMENTED", "message": "Метод пока не реализован на этапе 2"}})

    def do_POST(self):
        self.send_json(501, {"error": {"code": "NOT_IMPLEMENTED", "message": "POST методы будут реализованы на этапе 3"}})

    def send_json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        # Небольшой журнал запросов Docker Compose; персональных данных нет.
        super().log_message(format, *args)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8080), MockHandler)
    print("Stage 2 mock-api listening on 0.0.0.0:8080", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
