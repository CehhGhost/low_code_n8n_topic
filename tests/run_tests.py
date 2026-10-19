#!/usr/bin/env python3
"""Behavioral grading for n8n Workflow Lab (Python 3.10+, stdlib only).

No access to n8n internals is required: all evidence comes from the public
webhook and the provided Mock API. The grading server state is reset between
independent cases; do not run alongside other tests or manual exercises.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_WEBHOOK = "http://127.0.0.1:5678/webhook/support-request"
DEFAULT_MOCK = "http://127.0.0.1:8080"

USERS = {
    451: {"name": "Иван Иванов", "department": "IT"},
    452: {"name": "Мария Смирнова", "department": "Finance"},
    453: {"name": "Петр Орлов", "department": "Security"},
}
GROUPS = {"technical": "Technical Support", "billing": "Finance", "access": "Security"}
SLAS = {"high": 1, "medium": 4, "low": 24}


class CheckFailure(AssertionError):
    """An observable mismatch between actual and specified behavior."""


class TransportFailure(Exception):
    """Unreachable server, timeout, or invalid service response."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def request(method: str, url: str, payload: dict | None = None, timeout: float = 20.0) -> tuple[int, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except HTTPError as err:
        status, raw = err.code, err.read()
    except (URLError, TimeoutError, OSError) as err:
        raise TransportFailure(f"Нет соединения с {url}: {err}") from err
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        snippet = raw[:160].decode("utf-8", errors="replace")
        raise CheckFailure(f"Ожидался JSON от {url}, HTTP {status}; тело: {snippet!r}") from err
    return status, parsed


@dataclass
class TestResult:
    test_id: str
    category: str
    points: int
    passed: bool
    detail: str
    seconds: float


class Lab:
    def __init__(self, webhook: str, mock: str, timeout: float):
        self.webhook = webhook.rstrip("/")
        self.mock = mock.rstrip("/")
        self.timeout = timeout

    def api(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        return request(method, self.mock + path, payload, self.timeout)

    def reset(self) -> None:
        status, body = self.api("POST", "/__test__/reset", {})
        check(status == 200 and isinstance(body, dict) and body.get("status") == "reset",
              "Служебный сброс Mock API не работает")

    def fault(self, method: str, path: str, statuses: list[int], repeat: bool = False) -> None:
        status, body = self.api("POST", "/__test__/failures", {
            "method": method, "path": path, "statuses": statuses, "repeatLast": repeat,
        })
        check(status == 200 and isinstance(body, dict) and body.get("status") == "configured",
              f"Не удалось настроить сбой {method} {path}")

    def records(self) -> list[dict]:
        status, data = self.api("GET", "/__test__/records")
        check(status == 200 and isinstance(data, dict) and isinstance(data.get("records"), list),
              "Служебный /__test__/records вернул неожиданный ответ")
        return data["records"]

    def calls(self) -> list[dict]:
        status, data = self.api("GET", "/__test__/calls")
        check(status == 200 and isinstance(data, dict) and isinstance(data.get("calls"), list),
              "Служебный /__test__/calls вернул неожиданный ответ")
        return data["calls"]

    def send(self, payload: dict) -> tuple[int, object]:
        return request("POST", self.webhook, payload, self.timeout)


def form(request_id="REQ-1024", user_id=451, kind="technical", priority="high", description="Не работает авторизация") -> dict:
    return {"requestId": request_id, "userId": user_id, "type": kind,
            "priority": priority, "description": description}


def matching_calls(lab: Lab, method: str, path: str) -> list[dict]:
    return [c for c in lab.calls() if c.get("method") == method and c.get("path") == path]


def response(lab: Lab, payload: dict, *, status: int = 201) -> dict:
    code, obj = lab.send(payload)
    check(code == status, f"Ожидался HTTP {status}, получен HTTP {code}; ответ: {obj!r}")
    check(isinstance(obj, dict), f"Ответ HTTP {status} должен быть JSON-объектом")
    return obj


def error_response(lab: Lab, payload: dict, status: int, error_code: str) -> None:
    obj = response(lab, payload, status=status)
    err = obj.get("error")
    check(isinstance(err, dict), f"Ответ об ошибке должен содержать объект error: {obj!r}")
    check(err.get("code") == error_code, f"Ожидался error.code={error_code}, получено {err.get('code')!r}")
    check(isinstance(err.get("message"), str) and bool(err["message"].strip()),
          "Нужно непустое поле error.message")


def no_side_effect(lab: Lab) -> None:
    calls = lab.calls()
    check(not any(c.get("method") == "GET" and str(c.get("path", "")).startswith("/users/")
                  or c.get("method") == "POST" and c.get("path") == "/requests" for c in calls),
          f"Некорректное обращение вызвало внешнее API: {calls!r}")
    check(not lab.records(), "Некорректное обращение было зарегистрировано")


def expected_record(p: dict) -> dict:
    u = USERS[p["userId"]]
    return {**p, "userName": u["name"], "department": u["department"],
            "assignedGroup": GROUPS.get(p["type"], "General Support"),
            "slaHours": SLAS[p["priority"]], "status": "registered"}


def check_record_matches(record: dict, p: dict) -> None:
    for key, val in expected_record(p).items():
        check(type(record.get(key)) is type(val) and record.get(key) == val,
              f"Запись: поле {key}: ожидалось {val!r}, получено {record.get(key)!r}")


def check_success_body(body: dict, p: dict) -> None:
    expected = expected_record(p)
    for key in ("requestId", "status", "assignedGroup", "slaHours", "userName"):
        val = expected[key]
        check(type(body.get(key)) is type(val) and body.get(key) == val,
              f"Ответ: поле {key}: ожидалось {val!r}, получено {body.get(key)!r}")


# --- Base functional grading: 18 fixed groups, total 60 points. ---

def val01(lab):
    lab.reset()
    p = form()
    obj = response(lab, p)
    check(obj.get("requestId") == p["requestId"], "Ответ содержит чужой requestId")


def val02(lab):
    for key in form():
        lab.reset()
        p = form(); del p[key]
        error_response(lab, p, 400, "VALIDATION_ERROR")
        no_side_effect(lab)


def val03(lab):
    invalid = [
        {"userId": "451"}, {"userId": 451.5}, {"userId": True},
        {"requestId": 123}, {"type": 42}, {"description": ["error"]},
    ]
    for change in invalid:
        lab.reset()
        error_response(lab, {**form(), **change}, 400, "VALIDATION_ERROR")
        no_side_effect(lab)


def val04(lab):
    invalid = [
        {"requestId": "   "}, {"type": ""}, {"description": "  "},
        {"priority": "urgent"}, {"priority": "HIGH"},
        {"userId": 0}, {"userId": -3},
    ]
    for change in invalid:
        lab.reset()
        error_response(lab, {**form(), **change}, 400, "VALIDATION_ERROR")
        no_side_effect(lab)


def usr01(lab):
    lab.reset()
    p = form(user_id=451)
    response(lab, p)
    check(len(matching_calls(lab, "GET", "/users/451")) >= 1, "Нет GET /users/451")
    records = lab.records()
    check(len(records) == 1, "Ожидалась одна зарегистрированная запись")
    for k in ("userName", "department"):
        check(records[0].get(k) == USERS[451]["name" if k == "userName" else "department"],
              f"Неверное поле {k} в зарегистрированном обращении")


def usr02(lab):
    lab.reset()
    error_response(lab, form(user_id=9999), 404, "USER_NOT_FOUND")
    check(bool(matching_calls(lab, "GET", "/users/9999")), "Нет запроса GET /users/9999")
    check(not matching_calls(lab, "POST", "/requests"), "Нельзя регистрировать обращение неизвестного пользователя")
    check(not lab.records(), "Сохранено обращение неизвестного пользователя")


def route(lab, kind, expected):
    lab.reset()
    p = form(kind=kind)
    obj = response(lab, p)
    check(obj.get("assignedGroup") == expected, f"Ответ: ожидалась группа {expected}, получено {obj.get('assignedGroup')!r}")
    r = lab.records()
    check(len(r) == 1 and r[0].get("assignedGroup") == expected, "Неверная группа в сохраненном обращении")


def sla(lab, priority, expected):
    lab.reset()
    obj = response(lab, form(priority=priority))
    check(type(obj.get("slaHours")) is int and obj.get("slaHours") == expected,
          f"Ответ: ожидалось SLA={expected}, получено {obj.get('slaHours')!r}")
    r = lab.records()
    check(len(r) == 1 and type(r[0].get("slaHours")) is int and r[0].get("slaHours") == expected,
          "Неверный SLA в сохраненной записи")


def reg01(lab):
    lab.reset()
    p = form(request_id="REQ-REG-01", user_id=452, kind="billing", priority="medium", description="Ошибочное списание")
    response(lab, p)
    calls = matching_calls(lab, "POST", "/requests")
    check(len(calls) == 1 and calls[0].get("status") == 201, f"Требуется один успешный POST /requests; вызовы: {calls!r}")
    records = lab.records()
    check(len(records) == 1, f"Должна появиться ровно одна запись, получено {len(records)}")
    check_record_matches(records[0], p)


def reg02(lab):
    lab.reset()
    p = form(request_id="REQ-REG-02", user_id=453, kind="access", priority="low")
    body = response(lab, p)
    check_success_body(body, p)
    check(len(lab.records()) == 1, "Ответ об успехе не подтвержден записью в Mock API")


def reg03(lab):
    lab.reset()
    p1 = form(request_id="REQ-A", user_id=451, kind="technical", priority="high")
    p2 = form(request_id="REQ-B", user_id=453, kind="access", priority="low", description="Нет доступа")
    a = response(lab, p1)
    b = response(lab, p2)
    check_success_body(a, p1)
    check_success_body(b, p2)
    records = lab.records()
    check(len(records) == 2, f"Ожидались две отдельные записи, получено {len(records)}")
    check_record_matches(records[0], p1)
    check_record_matches(records[1], p2)


def err01(lab):
    lab.reset()
    lab.fault("GET", "/users/451", [503], repeat=True)
    error_response(lab, form(), 502, "UPSTREAM_ERROR")
    check(bool(matching_calls(lab, "GET", "/users/451")), "Должен быть выполнен GET /users/451")
    check(not matching_calls(lab, "POST", "/requests"), "Нельзя регистрировать обращение, если пользователь не найден из-за сбоя")
    check(not lab.records(), "Не должно быть сохраненных обращений")


def err02(lab):
    lab.reset()
    lab.fault("POST", "/requests", [503], repeat=True)
    error_response(lab, form(), 502, "UPSTREAM_ERROR")
    check(bool(matching_calls(lab, "POST", "/requests")), "Должен быть хотя бы один POST /requests")
    check(not lab.records(), "Ложная регистрация при HTTP 503")


BASE_CASES = [
    ("VAL-01", "Валидация", 3, val01),
    ("VAL-02", "Валидация", 3, val02),
    ("VAL-03", "Валидация", 3, val03),
    ("VAL-04", "Валидация", 3, val04),
    ("USR-01", "Пользователь", 4, usr01),
    ("USR-02", "Пользователь", 4, usr02),
    ("ROUTE-01", "Маршрутизация", 3, lambda lab: route(lab, "technical", "Technical Support")),
    ("ROUTE-02", "Маршрутизация", 3, lambda lab: route(lab, "billing", "Finance")),
    ("ROUTE-03", "Маршрутизация", 3, lambda lab: route(lab, "access", "Security")),
    ("ROUTE-04", "Маршрутизация", 3, lambda lab: route(lab, "other", "General Support")),
    ("SLA-01", "SLA", 3, lambda lab: sla(lab, "high", 1)),
    ("SLA-02", "SLA", 3, lambda lab: sla(lab, "medium", 4)),
    ("SLA-03", "SLA", 3, lambda lab: sla(lab, "low", 24)),
    ("REG-01", "Регистрация", 4, reg01),
    ("REG-02", "Регистрация", 3, reg02),
    ("REG-03", "Регистрация", 2, reg03),
    ("ERR-01", "Ошибки API", 5, err01),
    ("ERR-02", "Ошибки API", 5, err02),
]
assert len(BASE_CASES) == 18 and sum(c[2] for c in BASE_CASES) == 60


# --- Expert checks: no points; all eight checks required for grade Э. ---

def expert_retry_setup(lab: Lab, statuses: list[int], repeat_last: bool):
    lab.reset()
    lab.fault("POST", "/requests", statuses, repeat_last)
    status, body = lab.send(form())
    posts = matching_calls(lab, "POST", "/requests")
    records = lab.records()
    return status, body, posts, records


def retry_success(lab, failures: int):
    status, body, posts, records = expert_retry_setup(lab, [503] * failures, False)
    check(status == 201, f"После {failures} временных ошибок ожидается HTTP 201, получено {status}: {body!r}")
    check(len(posts) == failures + 1, f"Ожидалось {failures+1} попыток, обнаружено {len(posts)}")
    check([p.get("status") for p in posts] == [503] * failures + [201], "Неверная последовательность HTTP-статусов")
    check(len(records) == 1, "Должна быть только одна регистрация")
    check_success_body(body, form())


def retry_exhausted(lab):
    status, body, posts, records = expert_retry_setup(lab, [503], True)
    check(status == 502, f"При постоянном 503 ожидался HTTP 502, получено {status}")
    check(isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("code") == "UPSTREAM_ERROR", "Неверный формат ошибки при исчерпании попыток")
    check(len(posts) == 3 and all(p.get("status") == 503 for p in posts),
          f"Ожидалось ровно три попытки при постоянном 503; получено {len(posts)}")
    check(not records, "Не должно быть сохраненных обращений")


def retry_permanent(lab):
    status, body, posts, records = expert_retry_setup(lab, [400], True)
    check(status == 502, f"При HTTP 400 ожидался HTTP 502, получено {status}")
    check(isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("code") == "UPSTREAM_ERROR", "Неверная ошибка при HTTP 400")
    check(len(posts) == 1 and posts[0].get("status") == 400,
          f"Не следует повторять HTTP 400; получено {len(posts)} попыток")
    check(not records, "Обращение не должно появляться после 400")


def retry_delay(lab):
    status, _, posts, records = expert_retry_setup(lab, [503, 503], False)
    check(status == 201, f"После двух ошибок ожидается 201, получено {status}")
    check(len(posts) == 3 and len(records) == 1, "Не удалось получить три вызова и одну запись")
    gaps = [posts[i+1]["timestampMs"] - posts[i]["timestampMs"] for i in (0, 1)]
    check(all(g >= 400 for g in gaps),
          f"Минимальная задержка между повторами 400 мс; фактически: {gaps!r} мс")


def dedup_first(lab):
    lab.reset()
    p = form(request_id="REQ-DEDUP")
    body = response(lab, p)
    check_success_body(body, p)
    check(len(lab.records()) == 1 and len(matching_calls(lab, "POST", "/requests")) == 1,
          "Первое обращение должно зарегистрироваться ровно один раз")


def dedup_duplicate(lab):
    lab.reset()
    p = form(request_id="REQ-DEDUP")
    response(lab, p)
    status, body = lab.send(p)
    check(status == 200, f"Повторный запрос должен вернуть HTTP 200, получено {status}")
    check(isinstance(body, dict) and body.get("requestId") == p["requestId"] and body.get("status") == "already_registered",
          "Неверный ответ повторному обращению")
    check(len(lab.records()) == 1, "Повторно сохранено обращение")
    check(len(matching_calls(lab, "POST", "/requests")) == 1, "Дубликат вызвал повторный POST /requests")


def dedup_next(lab):
    lab.reset()
    p = form(request_id="REQ-DEDUP")
    response(lab, p)
    lab.send(p)
    other = form(request_id="REQ-NEXT", user_id=452, kind="billing", priority="medium")
    body = response(lab, other)
    check_success_body(body, other)
    records = lab.records()
    check(len(records) == 2 and {r.get("requestId") for r in records} == {"REQ-DEDUP", "REQ-NEXT"},
          "Новая заявка после дубликата не зарегистрирована независимо")


EXPERT_CASES = [
    ("EXP-R01", "Retry", 0, lambda lab: retry_success(lab, 1)),
    ("EXP-R02", "Retry", 0, lambda lab: retry_success(lab, 2)),
    ("EXP-R03", "Retry", 0, retry_exhausted),
    ("EXP-R04", "Retry", 0, retry_permanent),
    ("EXP-R05", "Retry", 0, retry_delay),
    ("EXP-D01", "Дубликаты", 0, dedup_first),
    ("EXP-D02", "Дубликаты", 0, dedup_duplicate),
    ("EXP-D03", "Дубликаты", 0, dedup_next),
]


def execute(lab: Lab, cases, verbose: bool) -> list[TestResult]:
    output: list[TestResult] = []
    for test_id, category, points, fn in cases:
        start = time.perf_counter()
        try:
            fn(lab)
        except (CheckFailure, TransportFailure, AssertionError, KeyError, TypeError, ValueError) as exc:
            ok, detail = False, str(exc) or exc.__class__.__name__
        except Exception as exc:  # Never accidentally award points after unexpected test crash.
            ok, detail = False, f"Ошибка тестового раннера: {type(exc).__name__}: {exc}"
        else:
            ok, detail = True, "Все проверки пройдены"
        result = TestResult(test_id, category, points, ok, detail, round(time.perf_counter() - start, 3))
        output.append(result)
        point_label = f"{points if ok else 0:>2}/{points:<2}" if points else "эксп."
        print(f"[{'PASS' if ok else 'FAIL'}] {test_id:8} {category:15} "
              f"{point_label:5} {result.seconds:.2f}s" +
              (f"  {detail}" if verbose or not ok else ""))
    return output


def summarize(results: list[TestResult], mode: str, lab: Lab) -> dict:
    basic = [r for r in results if r.test_id.startswith(("VAL", "USR", "ROUTE", "SLA", "REG", "ERR"))]
    expert = [r for r in results if r.test_id.startswith("EXP-")]
    base_points = sum(r.points for r in basic if r.passed)
    base_max = sum(r.points for r in basic)
    groups = {}
    for r in basic:
        entry = groups.setdefault(r.category, {"earned": 0, "possible": 0})
        entry["possible"] += r.points
        if r.passed:
            entry["earned"] += r.points
    expert_ok = bool(expert) and len(expert) == 8 and all(r.passed for r in expert)
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode, "webhook_url": lab.webhook, "mock_api_url": lab.mock,
        "functional_points": base_points, "functional_max_in_run": base_max,
        "functional_total_possible": 60,
        "functional_by_category": groups,
        "expert_checks_passed": sum(r.passed for r in expert),
        "expert_checks_in_run": len(expert), "expert_complete": expert_ok,
        "final_grade": None,
        "grade_note": "Итоговый уровень невозможен без ручной оценки видео (25), отчета (10) и оформления (5).",
        "tests": [asdict(r) for r in results],
    }


def markdown_report(data: dict) -> str:
    rows = [
        "# Отчет функциональных проверок n8n Workflow Lab", "",
        f"Дата (UTC): {data['generated_utc']}", "",
        f"Режим: `{data['mode']}`", "",
        f"- Функциональность: **{data['functional_points']}/{data['functional_max_in_run']}** баллов в данном запуске (из 60 возможных).",
        f"- Экспертные проверки: **{data['expert_checks_passed']}/{data['expert_checks_in_run']}**.",
        "- Видеоотчет (25 баллов), письменный отчет (10 баллов) и оформление workflow (5 баллов) оцениваются отдельно.",
        "- **Этот отчет не является итоговой оценкой за работу.**", "",
        "| Тест | Раздел | Результат | Баллы | Комментарий |", "|---|---|---|---:|---|",
    ]
    for r in data["tests"]:
        detail = r["detail"].replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {r['test_id']} | {r['category']} | {'PASS' if r['passed'] else 'FAIL'} | "
                    f"{r['points'] if r['passed'] else 0}/{r['points']} | {detail} |")
    rows.extend(["", "## Ограничения", "", "Тесты проверяют внешнее поведение в локальном окружении. "
                 "Сдача также должна содержать доступный видеоотчет, заполненный REPORT.md и экспорт workflow.", ""])
    return "\n".join(rows)


def preflight(lab: Lab) -> None:
    status, data = lab.api("GET", "/health")
    if status != 200 or not isinstance(data, dict) or data.get("status") != "ok":
        raise TransportFailure(f"Mock API не готов: HTTP {status}, ответ {data!r}")
    for path in ("/__test__/records", "/__test__/calls"):
        status, _ = lab.api("GET", path)
        if status != 200:
            raise TransportFailure(f"Отсутствует служебный метод Mock API: {path} (HTTP {status})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Проверка домашнего задания n8n через Webhook и Mock API")
    parser.add_argument("--mode", choices=("base", "expert", "all"), default="base",
                        help="base: 18 тестов/60 баллов; expert: 8 проверок; all: оба набора")
    parser.add_argument("--webhook-url", default=DEFAULT_WEBHOOK,
                        help=f"Рабочий Webhook n8n (по умолчанию {DEFAULT_WEBHOOK})")
    parser.add_argument("--mock-url", default=DEFAULT_MOCK,
                        help=f"Mock API на хосте (по умолчанию {DEFAULT_MOCK})")
    parser.add_argument("--timeout", type=float, default=25,
                        help="Таймаут одного запроса в секундах (по умолчанию 25)")
    parser.add_argument("--output-dir", default="reports", help="Каталог для JSON и Markdown отчета")
    parser.add_argument("--verbose", action="store_true", help="Печатать детали успешных тестов")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout должен быть положительным")
    lab = Lab(args.webhook_url, args.mock_url, args.timeout)
    try:
        preflight(lab)
    except (CheckFailure, TransportFailure) as exc:
        print(f"[ERROR] {exc}\nЗапустите 'docker compose up -d' и проверьте Mock API.", file=sys.stderr)
        return 2
    print("\n=== n8n Workflow Lab: функциональные проверки ===")
    print(f"Mock API: {lab.mock}\nWebhook:  {lab.webhook}\n")
    cases = (BASE_CASES if args.mode == "base" else EXPERT_CASES if args.mode == "expert" else BASE_CASES + EXPERT_CASES)
    results = execute(lab, cases, args.verbose)
    report = summarize(results, args.mode, lab)
    path = Path(args.output_dir)
    path.mkdir(parents=True, exist_ok=True)
    json_path = path / "latest-results.json"
    md_path = path / "latest-results.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print("\n=== Результат ===")
    if args.mode != "expert":
        for category, score in report["functional_by_category"].items():
            print(f"  {category:18} {score['earned']:2}/{score['possible']}")
        print(f"Функциональность: {report['functional_points']}/60 баллов")
    if args.mode != "base":
        print(f"Экспертные проверки: {report['expert_checks_passed']}/8")
    print(f"Отчеты: {json_path}, {md_path}")
    print("Важно: итоговый уровень Б/Б+/П/Э определяется с учетом обязательного видео, отчета и оформления.")
    # Failed checks are a normal result of self-assessment; nonzero exit is CI-friendly.
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
