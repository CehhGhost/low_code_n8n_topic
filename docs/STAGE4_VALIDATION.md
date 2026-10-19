# Этап 4. Проверка стартовой заготовки и эталона

## 1. Обновить проект

Распакуйте **студенческий архив этапа 4** поверх предыдущей версии проекта (не удаляя Docker volume). Убедитесь, что Mock API из этапа 3 работает:

```bash
python tests/smoke_mock_api.py
```

## 2. Стартовый workflow

1. В n8n откройте новый workflow и импортируйте `workflows/student-starter.json`.
2. Сохраните и **опубликуйте** workflow, чтобы был доступен production Webhook.
3. Выполните POST на `http://localhost:5678/webhook/support-request`:

```bash
curl -i -X POST http://localhost:5678/webhook/support-request \
  -H 'Content-Type: application/json' \
  -d '{"requestId":"REQ-1024","userId":451,"type":"technical","priority":"high","description":"Учебный запрос"}'
```

Пока ожидается **HTTP 200** с полем `message`, начинающимся на `TODO`. Это нормально: стартовый workflow не содержит решения. Если ответ 404, проверьте публикацию workflow. Если n8n сообщает о конфликте пути webhook, отключите другой опубликованный workflow с тем же путем.

## 3. Эталонный базовый workflow (только эксперту)

В отдельном архиве для эксперта находится `workflows/reference-baseline.json`. **Не добавляйте его в студенческий репозиторий**. Отключите стартовый workflow перед публикацией эталона.

```bash
# Перед запуском каждого независимого теста сбросьте состояние
curl -X POST http://127.0.0.1:8080/__test__/reset \
  -H 'Content-Type: application/json' -d '{}'

# Успешный запрос - ожидается HTTP 201
curl -i -X POST http://localhost:5678/webhook/support-request \
  -H 'Content-Type: application/json' \
  -d '{"requestId":"REQ-1024","userId":451,"type":"technical","priority":"high","description":"Учебный запрос"}'

# Проверка фактической регистрации
curl http://127.0.0.1:8080/__test__/records
```

Дополнительные ручные проверки:

- Передайте `userId: 9999`: ожидается `404 USER_NOT_FOUND`, без записи в Mock API.
- Не указывайте `priority`: ожидается `400 VALIDATION_ERROR`, без внешних вызовов.
- Настройте `503` для `GET /users/451` через `POST /__test__/failures`: ожидается `502 UPSTREAM_ERROR`.
- Настройте постоянный `503` для `POST /requests`: ожидается `502 UPSTREAM_ERROR` и отсутствие записи.

Полная автоматизированная проверка по 18 группам из `TEST_CASES.md` будет создана на следующем этапе, поэтому результаты этой ручной проверки не заменяют итогового тестового отчета.

## 4. Статические проверки (без Docker)

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Этот набор проверяет контракт Mock API, структуру стартового workflow и обучающие материалы. Проверки не подтверждают выполнение эталонного workflow в n8n.
