# Выбор модели в Docker

> Исторический документ до упрощения 1.7. Актуальный состав и ограничения: [SIMPLIFIED-1.7.md](SIMPLIFIED-1.7.md). Упомянутые здесь LLM, GitHub, ATS-заметки и проценты больше не входят в runtime.

Модель нужна только для черновиков тестовых вопросов через HR API. Анализ резюме и весь сценарий MAX работают без неё. Код провайдера отделён в `hiring/llm.py`, правила вопросов и квоты находятся в `hiring/ai_questions.py`.

Настройки читаются из окружения при запуске. Docker Compose получает их из приватного `.env.hiring`. Образ один и тот же для всех провайдеров. Пересборка при смене модели не нужна:

```sh
docker compose up -d --no-deps --force-recreate api
```

На production используйте тот же набор `--env-file` и `-f`, что при первом запуске, чтобы не потерять production-overlay. Не выводите `docker compose config` без `--quiet`: он раскрывает заполненные секреты.

## Режимы

### Без внешней модели

```dotenv
HIRING_LLM_PROVIDER=disabled
```

По умолчанию нет сетевых LLM-запросов. API возвращает локальный шаблон с `source=local`.

### Cloud.ru, GigaChat или другая модель каталога

```dotenv
HIRING_LLM_PROVIDER=cloudru
HIRING_LLM_MODEL=ai-sage/GigaChat3-10B-A1.8B
HIRING_LLM_BASE_URL=https://foundation-models.api.cloud.ru/v1
CLOUDRU_API_KEY=заполнить_только_локально
```

Другую модель каталога выбирает оператор через `HIRING_LLM_MODEL`. Она должна поддерживать обычные Chat Completions и текстовый JSON-ответ в заданном бюджете. Доступность модели/качество её вопросов требуют отдельной проверки. Ключ Cloud.ru отправляется только на фиксированный origin Cloud.ru.

### DeepSeek

```dotenv
HIRING_LLM_PROVIDER=deepseek
HIRING_LLM_BASE_URL=https://api.deepseek.com/v1
HIRING_LLM_MODEL=идентификатор_доступной_вам_модели
LLM_API_KEY=отдельный_ключ_DeepSeek
```

ID модели нужно выбрать по актуальному аккаунту и документации провайдера, а не считать любой старый alias доступным. Режимы с длинным reasoning могут не уложиться в бюджет. Cloud.ru-ключ для DeepSeek не используется. Реальная проверка DeepSeek в этой доработке не выполнялась, протокол проверен на подменённом HTTP-транспорте с синтетическими ответами.

### Другой OpenAI-compatible сервис

```dotenv
HIRING_LLM_PROVIDER=openai_compatible
HIRING_LLM_BASE_URL=https://api.provider.example/v1
HIRING_LLM_ALLOWED_HOSTS=api.provider.example
HIRING_LLM_MODEL=идентификатор_модели
LLM_API_KEY=отдельный_ключ_этого_сервиса
```

Адрес выше иллюстративный, его нужно заменить. Поддерживается `POST {base_url}/chat/completions` с Bearer-авторизацией и текстовым `choices[0].message.content`. Требуется точный разрешённый hostname, HTTPS на 443, без query/fragment/учётных данных в URL. HTTP, IP-адреса и localhost не поддерживаются. Это доверенная настройка оператора, а не параметр входящего запроса пользователя. Нативный Sber GigaChat OAuth, Anthropic Messages и локальный Ollama по HTTP этим адаптером не поддерживаются.

Условия и документированность сервиса проверяет оператор перед выбором, в том числе по правилам хакатона. Код не регистрирует аккаунты, не ищет бесплатные ключи и не переключает провайдеров автоматически.

## Бюджет

| Переменная | По умолчанию | Значение |
|---|---:|---|
| HIRING_LLM_MAX_TOKENS | 1000 | Максимум выходных токенов, 128-2500 |
| HIRING_LLM_TIMEOUT_SECONDS | 12 | Сетевой timeout, 2-30 секунд |
| HIRING_LLM_OWNER_DAILY_LIMIT | 3 | Новые запросы работодателя за скользящие 24 часа |
| HIRING_LLM_DAILY_LIMIT | 10 | Новые запросы всей базы за 24 часа |
| HIRING_LLM_TOTAL_LIMIT | 50 | Всего резервов запросов в этой базе |

Квоты положительные: owner daily <= daily <= total <= 10000. Они хранятся в SQLite, учитывают сбои и все провайдеры вместе. Смена модели или перезапуск не обнуляют их. Восстановление старого backup может откатить счётчик: дополнительно ограничьте расходы в кабинете провайдера. Это лимиты запросов, не денежный баланс.

Кэш изолирован по работодателю, провайдеру, endpoint, модели, навыкам, уровню, лимиту токенов и версии prompt. Срок 24 часа. Успешный кэш доступен даже после исчерпания квоты. Неудачный запрос по тому же ключу не повторяется автоматически в течение 24 часов. Один активный резерв на 2 минуты защищает от параллельной генерации. Новое обращение занимает резерв до выхода в сеть.

## API и проверка

Все методы требуют интеграционного права `tests:write`:

- `GET /api/integrations/v1/tests/ai-skills`: допустимые названия навыков.
- `GET /api/integrations/v1/tests/ai-status`: провайдер, модель, наличие ключа, лимит и доступные новые запросы. Без ключей, без имён других работодателей, без обращения к LLM. `ready` означает наличие конфигурации, а не проверку доступности провайдера. Параллельный запрос может израсходовать квоту после чтения статуса.
- `POST /api/integrations/v1/tests/ai-draft`: черновик по 1-5 навыкам, enum-уровню и обязательному `allow_external_generation: true`. Свободные резюме, ссылки и переписка запрещены схемой.

```json
{"skills":["python","sql"],"level":"junior","allow_external_generation":true}
```

Ответ содержит `draft`, `provider`, `model`, `cached`, `review_required: true`, `published: false`. Для совместимости `source=gigachat` остаётся у исходной модели GigaChat через Cloud.ru, у остальных моделей `source=llm`. Локальные вопросы имеют `source=local` и `provider=null`. Это явный fallback при отсутствии ключа, ошибках сети, HTTP или JSON. Достижение квоты возвращает 429, занятость генератора 409. Вопросы проверяет человек и отдельно сохраняет через `POST /tests`. В MAX остаётся библиотека тестов, отдельной кнопки AI-генерации нет.

```sh
# Без сети и списаний, печатает только безопасные поля конфигурации:
python scripts/smoke_llm.py
# Явная проверка максимум одним внешним запросом на синтетических навыках:
python scripts/smoke_llm.py --live
```

Live-smoke использует отдельный постоянный файл `data/hiring.ai-smoke.db` и его собственные квоты, не рабочую базу. Он не отправляет сообщения MAX, не читает реальные резюме и не печатает ключ. В Docker скрипт также доступен через `docker compose exec api python scripts/smoke_llm.py`.

Старые `HIRING_GIGACHAT_ENABLED=true` и `CLOUDRU_API_KEY` временно поддерживаются, если `HIRING_LLM_PROVIDER` вообще отсутствует. Явный `disabled` всегда отключает старую настройку. Имя скрипта изменено с `smoke_gigachat.py` на `smoke_llm.py`.

Источники: [Cloud.ru API](https://cloud.ru/docs/foundation-models/ug/topics/api-ref__specs), [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/).
