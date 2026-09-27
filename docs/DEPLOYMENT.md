# Перенос на Linux VM российского провайдера

Сервер пока не настроен. Этот документ — инструкция, а не подтверждение доступного production.

Коротко, какие сведения и доступы нужны от владельца: [SUBMISSION-ACCESS.md](SUBMISSION-ACCESS.md). Модель, endpoint и бюджеты задаются через [LLM.md](LLM.md), пересборка Docker для смены модели не нужна. Базовый сценарий работает при `HIRING_LLM_PROVIDER=disabled`.

Подходит для Яндекс Cloud, Cloud.ru и обычного VPS с Docker. Актуальная проверка пробных предложений: [HOSTING-OPTIONS-2026-09-27.md](HOSTING-OPTIONS-2026-09-27.md). Создание платёжного аккаунта и выбор тарифа выполняет владелец.

## До запуска

1. Подтвердить доступ к VM и её SSH host key через доверенную консоль. Ранее для старой VM обнаружена смена ключа. Не отключать StrictHostKeyChecking и не заменять known_hosts без проверки.
2. Выбрать домен API, направить DNS на VM, открыть входящие TCP 80/443. SSH ограничить адресами администраторов. Не открывать SQLite или порт 8000 в интернет.
3. Установить Docker Engine + Compose на Linux. Для MVP планировать один экземпляр API, один sender и SQLite-volume. Фактический размер VM и бюджет согласовать с владельцем.
4. Скопировать `.env.example` в `.env.hiring` и заполнить отдельно от Git: токен MAX, имя бота, секрет JWT 32+ символа, код работодателя, webhook secret 24+ символа.
5. Задать `HIRING_ENV=production`, `HIRING_DEMO=false`, `PUBLIC_HOST=api.ваш-домен`, `PUBLIC_URL=https://api.ваш-домен`. В PUBLIC_URL не нужны порт, путь или параметры. Токен отправляется только в `https://platform-api2.max.ru`.
6. Обновить `.env.hiring` permissions: на Linux `chmod 600 .env.hiring`. Не выводить `docker compose config` без `--quiet` в публичные логи: развёрнутый конфиг содержит секреты.

## Запуск всех компонентов

```sh
docker compose --env-file .env.hiring -f docker-compose.yml -f compose.production.yml config --quiet
docker compose --env-file .env.hiring -f docker-compose.yml -f compose.production.yml up --build -d
```

Caddy получает доверенный сертификат для PUBLIC_HOST и проксирует в API. Проверить `https://домен/health` и `https://домен/openapi.json` с другого компьютера. Не использовать самоподписной сертификат. Для MAX webhook нужен HTTPS на 443.

Остановить локальный polling. Команда ниже сначала только показывает план, затем регистрирует webhook после проверки здоровья. Она не удаляет чужие подписки:

```sh
docker compose exec api python -m hiring.webhook_setup
docker compose exec api python -m hiring.webhook_setup --apply
```

Настройка включает `bot_started`, `message_created`, `message_callback`. После регистрации пройти `docs/ACCEPTANCE.md` в двух клиентах MAX. Тест «HTTP 200» не заменяет приёмку чата.

## Наблюдение и восстановление

```sh
docker compose ps
docker compose logs --tail=100 api
docker compose exec api python -m hiring.maintenance status
docker compose exec api python -m hiring.maintenance backup --output /app/data/backups/pre-update.db
docker compose exec api python -m hiring.maintenance prune
# Только после проверки списка и политики хранения:
docker compose exec api python -m hiring.maintenance prune --apply
```

Backup-команда делает согласованную SQLite-копию, проверяет integrity_check и отказывается перезаписывать файл. Выбрать новое имя для каждой копии. Копии содержат персональные данные, если они были в БД: хранить с ограниченным доступом вне публичных артефактов. Сохранить отдельную копию вне VM согласно согласованному регламенту.

Для восстановления: остановить API и sender, сохранить текущую базу отдельной backup-командой, восстановить проверенную копию **в новый volume/каталог**, указать его в конфигурации, проверить права UID 10001 и запустить один экземпляр. Сначала пройти проверку на синтетическом стенде. Не перезаписывать единственную рабочую базу и не удалять volume через `down -v`.

Операция отзыва сериализована с sender. Уже начатая отправка может закончиться до фиксации отзыва, но следующая отправка отозванного отклика отменяется. После таймаута внешнего API или аварии между отправкой и commit повтор уведомления возможен. Повторные бизнес-действия и события проверяются отдельно.

На момент релиза нет подключённого сервиса алертов. Владелец должен назначить ответственного и настроить мониторинг HTTPS, рестартов и количества failed/pending. Скрытые ошибки больше не игнорируются: журнал выводит HTTP-код/число попыток без текста резюме или токенов.

## Перед технической проверкой

Изолировать базу проверки от реальных/ручных MAX-аккаунтов. Подготовить три синтетические роли по `docs/DATA-API.md`, заменить baseUrl в YAML, дважды прогнать удалённый сценарий. Зафиксировать commit, время сборки, результаты smoke-теста и период доступности стенда.

Источники: [MAX webhook](https://dev.max.ru/docs-api/methods/POST/subscriptions), [Caddy body limit](https://caddyserver.com/docs/caddyfile/directives/request_body). Точные реквизиты облака и доступы в этот документ не включаются.
