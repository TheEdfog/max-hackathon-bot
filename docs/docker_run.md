# Запуск инфраструктуры РезюмИТ через Docker

Этот вариант запускает в Docker только инфраструктуру проекта:

- PostgreSQL
- Redis

FastAPI-приложение продолжает запускаться локально из `.venv`, как и раньше. Такой режим безопаснее для текущей разработки: код приложения, генерация PDF и DeepSeek остаются в привычной среде.

## Почему PostgreSQL на порту 5433

Текущий рабочий `.env` использует `localhost:5432`. Чтобы не сломать уже работающую базу и не получить конфликт портов, Docker PostgreSQL опубликован на `localhost:5433`.

Внутри контейнера PostgreSQL всё равно работает на стандартном порту `5432`, но с компьютера доступен как `localhost:5433`.

## Команды

Запустить только PostgreSQL и Redis:

```powershell
.\start_infra.ps1
```

Запустить сайт на текущей БД из `.env`:

```powershell
.\start_project.ps1
```

Запустить сайт с Docker PostgreSQL:

```powershell
.\start_project.ps1 -WithInfra -UseDockerDb
```

Запустить сайт с автоперезагрузкой кода:

```powershell
.\start_project.ps1 -WithInfra -UseDockerDb -Reload
```

Остановить контейнеры, не удаляя данные:

```powershell
.\stop_infra.ps1
```

## Строка подключения к Docker PostgreSQL

Если нужно вручную переключить `.env` на Docker PostgreSQL, укажите:

```env
DATABASE_URL=postgresql+psycopg://postgres:MyNewStrongPassword123!@localhost:5433/cvservice_db
```

Для возврата к текущему рабочему режиму верните прежний `DATABASE_URL` на `localhost:5432`.

## Хранение данных

Данные PostgreSQL хранятся в Docker volume:

```text
rezumit_postgres_data
```

Данные Redis хранятся в Docker volume:

```text
rezumit_redis_data
```

Команда `docker compose stop` данные не удаляет. Данные удаляются только при явном удалении volume.
