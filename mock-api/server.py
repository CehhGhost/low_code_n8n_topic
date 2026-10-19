"""Local deterministic Mock API for the n8n workflow laboratory.

Run with Python >=3.10, no third-party packages.  Data and fault scenarios
are deliberately in-memory: tests reset them between cases.  Not production API.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


USERS = {
    451: {"id": 451, "name": "Иван Иванов", "department": "IT", "email": "ivan@example.com"},
    452: {"id": 452, "name": "Мария Смирнова", "department": "Finance", "email": "maria@example.com"},
    453: {"id": 453, "name": "Петр Орлов", "department": "Security", "email": "petr@example.com"},
}

# Mock API accepts the resulting record, not the logic that computes it.
# Validating routing/SLA is the *test runner's* responsibility, not Mock API's.
RECORD_STRINGS = (
    "requestId", "userName", "department", "type", "priority",
    "description", "assignedGroup", "status",
)
RECORD_POSITIVE_INTS = ("userId", "slaHours")
MAX_BODY_BYTES = 1024 * 1024


def error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


class MockStore:
    """Synchronized mutable state for a ThreadingHTTPServer instance."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.records: list[dict] = []
            self.calls: list[dict] = []
            self.failures: dict[tuple[str, str], dict] = {}
            self.next_record_id = 1

    def configure_failure(self, method: str, path: str, statuses: list[int], repeat_last: bool) -> None:
        with self.lock:
            self.failures[(method, path)] = {
                "statuses": statuses[:], "repeatLast": repeat_last, "index": 0,
            }

    def simulated_status(self, method: str, path: str) -> int | None:
        with self.lock:
            failure = self.failures.get((method, path))
            if failure is None:
                return None
            index = failure["index"]
            if index < len(failure["statuses"]):
                failure["index"] += 1
                return failure["statuses"][index]
            if failure["repeatLast"]:
                return failure["statuses"][-1]
            return None

    def record_call(self, method: str, path: str, status: int, timestamp_ms: int) -> None:
        with self.lock:
            self.calls.append({
                "method": method, "path": path,
                "status": status, "timestampMs": timestamp_ms,
            })

    def save_record(self, data: dict) -> dict:
        with self.lock:
            record = {"recordId": f"rec-{self.next_record_id:06d}"}
            self.next_record_id += 1
            record.update({key: data[key] for key in RECORD_STRINGS + RECORD_POSITIVE_INTS})
            self.records.append(record)
            return copy.deepcopy(record)

    def get_record(self, request_id: str) -> dict | None:
        with self.lock:
            return next((copy.deepcopy(r) for r in self.records if r["requestId"] == request_id), None)

    def get_records(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.records)

    def get_calls(self) -> list[dict]:
        with self.lock:
            return copy.deepcopy(self.calls)


class MockHandler(BaseHTTPRequestHandler):
    # The server assigns its own store.  A default supports simple imports.
    store = MockStore()

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        timestamp = int(time.time() * 1000)
        if path == "/health":
            self.send_json(200, {"status": "ok", "service": "mock-api"})
            return
        if path == "/__test__/calls":
            self.send_json(200, {"calls": self.store.get_calls()})
            return
        if path == "/__test__/records":
            self.send_json(200, {"records": self.store.get_records()})
            return

        user_match = re.fullmatch(r"/users/(\d+)", path)
        record_match = re.fullmatch(r"/requests/([^/]+)", path)
        if user_match:
            user = USERS.get(int(user_match.group(1)))
            status, payload = (
                (200, user) if user is not None else
                (404, error("USER_NOT_FOUND", "Пользователь не найден"))
            )
        elif record_match:
            record = self.store.get_record(record_match.group(1))
            status, payload = (
                (200, record) if record is not None else
                (404, error("REQUEST_NOT_FOUND", "Обращение не найдено"))
            )
        else:
            self.send_json(404, error("NOT_FOUND", "Неизвестный endpoint"))
            return

        fault = self.store.simulated_status("GET", path)
        if fault is not None:
            status, payload = fault, error("SIMULATED_FAILURE", "Искусственная ошибка внешнего сервиса")
        self.store.record_call("GET", path, status, timestamp)
        self.send_json(status, payload)

    def do_POST(self) -> None:
        path = unquote(urlsplit(self.path).path)
        timestamp = int(time.time() * 1000)
        if path not in ("/requests", "/__test__/reset", "/__test__/failures"):
            self.send_json(404, error("NOT_FOUND", "Неизвестный endpoint"))
            return

        data, parse_error = self.read_json_object()
        if parse_error is not None:
            if path == "/requests":
                self.store.record_call("POST", path, 400, timestamp)
            self.send_json(400, error("INVALID_REQUEST", parse_error))
            return

        if path == "/__test__/reset":
            self.store.reset()
            self.send_json(200, {"status": "reset"})
            return

        if path == "/__test__/failures":
            problem = self.validate_failure_config(data)
            if problem is not None:
                self.send_json(400, error("INVALID_FAILURE_CONFIG", problem))
                return
            self.store.configure_failure(
                data["method"], data["path"], data["statuses"], data.get("repeatLast", False)
            )
            self.send_json(200, {"status": "configured"})
            return

        fault = self.store.simulated_status("POST", path)
        if fault is not None:
            self.store.record_call("POST", path, fault, timestamp)
            self.send_json(fault, error("SIMULATED_FAILURE", "Искусственная ошибка внешнего сервиса"))
            return

        problem = self.validate_record(data)
        if problem is not None:
            self.store.record_call("POST", path, 400, timestamp)
            self.send_json(400, error("INVALID_REQUEST", problem))
            return

        saved = self.store.save_record(data)
        self.store.record_call("POST", path, 201, timestamp)
        self.send_json(201, {
            "recordId": saved["recordId"],
            "requestId": saved["requestId"], "status": "registered",
        })

    def read_json_object(self) -> tuple[dict | None, str | None]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return None, "Отсутствует Content-Length"
        try:
            length = int(length_header)
        except ValueError:
            return None, "Некорректный Content-Length"
        if length < 0 or length > MAX_BODY_BYTES:
            return None, "Недопустимый размер тела запроса"
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "Ожидался корректный JSON"
        if not isinstance(data, dict):
            return None, "Ожидался JSON-объект"
        return data, None

    @staticmethod
    def validate_record(data: dict) -> str | None:
        for key in RECORD_STRINGS:
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                return f"Поле {key} должно быть непустой строкой"
        for key in RECORD_POSITIVE_INTS:
            value = data.get(key)
            if type(value) is not int or value <= 0:
                return f"Поле {key} должно быть положительным целым числом"
        if data["status"] != "registered":
            return "Поле status должно быть registered"
        return None

    @staticmethod
    def validate_failure_config(data: dict) -> str | None:
        method, path = data.get("method"), data.get("path")
        if method not in ("GET", "POST") or not isinstance(path, str):
            return "method должен быть GET или POST, path - строкой"
        valid_target = (
            (method == "POST" and path == "/requests") or
            (method == "GET" and (
                re.fullmatch(r"/users/\d+", path) or
                re.fullmatch(r"/requests/[^/]+", path)
            ))
        )
        if not valid_target:
            return "Сценарии ошибок допускаются только для основных API"
        statuses = data.get("statuses")
        if (not isinstance(statuses, list) or not statuses or
                any(type(code) is not int or code < 400 or code > 599 for code in statuses)):
            return "statuses должен быть непустым массивом HTTP-кодов 400..599"
        if type(data.get("repeatLast", False)) is not bool:
            return "repeatLast должен быть булевым значением"
        return None

    def send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # The test client may cancel a request; keep the local mock alive.
            pass


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), MockHandler)
    print("n8n lab mock-api listening on 0.0.0.0:8080", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
