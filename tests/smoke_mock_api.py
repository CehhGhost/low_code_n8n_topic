"""HTTP smoke-check of the *running Docker* Mock API, no external packages.

Usage from repository root: python tests/smoke_mock_api.py
NOTE: Clears only the Mock API's temporary data/failures, never n8n workflows.
"""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.environ.get("MOCK_API_URL", "http://127.0.0.1:8080").rstrip("/")


def call(method, path, data=None):
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    request = Request(
        BASE + path, data=payload, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        with exc:
            return exc.code, json.load(exc)


def run():
    code, result = call("GET", "/health")
    assert code == 200 and result.get("stage") == 3, "Mock API не обновлен до этапа 3"
    print("[1/6] Health: OK")
    code, result = call("GET", "/users/451")
    assert code == 200 and result["department"] == "IT"
    print("[2/6] GET /users/451: OK")

    code, _ = call("POST", "/__test__/reset", {})
    assert code == 200
    record = {
        "requestId": "SMOKE-1", "userId": 451,
        "userName": "Иван Иванов", "department": "IT", "type": "technical",
        "priority": "high", "description": "Проверка запуска Mock API",
        "assignedGroup": "Technical Support", "slaHours": 1,
        "status": "registered",
    }
    code, created = call("POST", "/requests", record)
    assert code == 201 and created["requestId"] == "SMOKE-1"
    code, found = call("GET", "/requests/SMOKE-1")
    assert code == 200 and found["recordId"] == created["recordId"]
    print("[3/6] Сохранение и чтение обращения: OK")

    code, _ = call("POST", "/__test__/failures", {
        "method": "POST", "path": "/requests", "statuses": [503, 503], "repeatLast": False,
    })
    assert code == 200
    attempts = [call("POST", "/requests", {**record, "requestId": "SMOKE-2"})[0] for _ in range(3)]
    assert attempts == [503, 503, 201], attempts
    print("[4/6] Имитация двух ошибок 503, затем успех: OK")

    code, ledger = call("GET", "/__test__/calls")
    posts = [item for item in ledger["calls"] if item["path"] == "/requests"]
    assert [item["status"] for item in posts] == [201, 503, 503, 201]
    assert all(isinstance(c["timestampMs"], int) for c in posts)
    print("[5/6] Журнал запросов и временные метки: OK")

    code, rows = call("GET", "/__test__/records")
    assert code == 200 and len(rows["records"]) == 2
    code, _ = call("POST", "/__test__/reset", {})
    assert code == 200
    assert call("GET", "/__test__/records")[1]["records"] == []
    print("[6/6] Получение записей и сброс состояния: OK")
    print("УСПЕХ: работающий Mock API соответствует базовым ожиданиям этапа 3.")


if __name__ == "__main__":
    try:
        run()
    except (AssertionError, URLError, KeyError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        print(f"Проверьте: docker compose ps; docker compose logs mock-api; адрес {BASE}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Protect next student's checks even if assertions fail halfway through.
        try:
            call("POST", "/__test__/reset", {})
        except (URLError, ValueError):
            pass
