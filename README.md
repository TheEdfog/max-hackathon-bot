# РезюмИТ

Веб-приложение для дипломного проекта: сервис помогает IT-соискателю сравнить свой профиль с вакансией, получить обоснованный match/scoring, рекомендации и адаптированное резюме.

## Что уже реализовано
- Авторизация через JWT-cookie.
- Один пользовательский email соответствует одному профилю кандидата.
- Несколько вакансий на пользователя.
- Вакансия добавляется либо текстом, либо URL.
- Если указан URL и не указан текст, сервер отправляет ссылку в DeepSeek для извлечения текста вакансии.
- Сырые тексты вакансий сохраняются в `storage/vacancy_raw/<hash>.txt`.
- В таблице `vacancies` хранится относительный путь `raw_data_path`.
- Из вакансии извлекаются требования `must` и `nice`.
- Аналитическая система считает `match`, `must_pct`, `nice_pct` и evidence по каждому требованию.
- Рекомендации делятся на два класса: что изменить в резюме и что нужно подучить или отработать.
- Генерируется резюме под выбранную пользователем вакансию.
- Резюме можно править естественным языком через DeepSeek.
- Доступен экспорт LaTeX, PDF собирается при наличии `xelatex` или `pdflatex`.

## Технологии
- Python 3.12
- FastAPI + Jinja2
- PostgreSQL + SQLAlchemy
- Redis
- Docker / Docker Compose
- DeepSeek через OpenAI-compatible API

## Конфигурация
Основной файл:

```text
config/app_config.toml
```

Можно переопределять параметры через `.env`. Пример находится в `.env.example`.

Ключ DeepSeek можно указать в одном из мест:

```toml
[llm]
api_key = "..."
```

или:

```text
DEEPSEEK_API_KEY=...
```

## Запуск
1. Установить зависимости:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. Создать PostgreSQL БД и проверить строку подключения:

```toml
[database]
url = "postgresql+psycopg://postgres:postgres@localhost:5432/cvservice_db"
```

3. Запустить сервер:

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.web.main:app --reload
```

4. Открыть сайт:

```text
http://127.0.0.1:8000
```

### Запуск через готовые PowerShell-скрипты

Обычный запуск на БД из `.env`:

```powershell
.\start_project.ps1
```

Запуск PostgreSQL и Redis в Docker:

```powershell
.\start_infra.ps1
```

Запуск сайта с Docker PostgreSQL:

```powershell
.\start_project.ps1 -WithInfra -UseDockerDb
```

Docker PostgreSQL опубликован на `localhost:5433`, чтобы не конфликтовать с текущей рабочей БД на `localhost:5432`.
Подробнее: `docs/docker_run.md`.

## Основной сценарий
1. Зарегистрироваться или войти.
2. Заполнить профиль кандидата.
3. Добавить вакансию текстом или ссылкой.
4. Перейти в раздел «Анализ».
5. Выбрать вакансию и получить match/scoring.
6. Посмотреть рекомендации.
7. Сгенерировать резюме.
8. Скачать LaTeX или PDF.

## Таблицы
- `users` — аккаунты.
- `profiles` — один профиль на пользователя.
- `profile_skills` — нормализованные навыки профиля.
- `vacancies` — вакансии и путь к сырому txt-файлу.
- `vacancy_requirements` — извлечённые требования `must`/`nice`.
- `generated_resumes` — сгенерированные резюме под конкретные вакансии.
- `user_settings` — служебная таблица, оставлена для совместимости.

## Формула evidence
Для каждого требования `r` считается:

```text
evidence_r = min(
    1,
    0.65 * exact_skill_match
    + 0.35 * related_skill_match
    + 0.25 * min(text_hits / 3, 1)
    + 0.10 * context_score
)
```

Где:
- `exact_skill_match` — навык есть в разделе hard skills.
- `related_skill_match` — в профиле есть родственный навык, например `sql` для `postgresql`, `mysql` или `mssql`.
- `text_hits` — сколько раз навык или родственные навыки встречаются в тексте профиля.
- `context_score` — есть ли навык в опыте, проектах, сертификатах или описании.

Итог:

```text
total_pct = 0.75 * must_pct + 0.25 * nice_pct
```

Если в вакансии есть только один класс требований, используется процент этого класса.
