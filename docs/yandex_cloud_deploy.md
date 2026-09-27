# Публикация в Yandex Cloud

Самый простой вариант для этого проекта: одна VM в Compute Cloud с Docker Compose. На ней запускаются FastAPI-приложение, PostgreSQL и Redis. Данные БД и файлы приложения сохраняются в Docker volumes.

Официальные разделы Yandex Cloud, которые полезны при настройке: [Compute Cloud](https://yandex.cloud/en/docs/compute), [Container Registry](https://yandex.cloud/en/docs/container-registry/quickstart/) и пример VM с Docker Compose на Container Optimized Image: [Creating a VM with multiple Docker containers](https://yandex.cloud/en/docs/compute/tutorials/docker-compose).

## 1. Создать VM

В консоли Yandex Cloud создайте виртуальную машину:

- образ: Ubuntu 22.04/24.04 или Container Optimized Image;
- ресурсы для дипломного стенда: 2 vCPU, 2-4 GB RAM, диск 20+ GB;
- публичный IP: Auto;
- доступ: SSH-ключ;
- security group: открыть входящие TCP `22` и `80`.

Если есть домен, позже добавьте A-запись на публичный IP VM.

## 2. Подключиться к серверу

```bash
ssh yc-user@<PUBLIC_IP>
```

Для обычного Ubuntu-образа установите Docker:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
```

## 3. Передать проект на VM

Вариант через Git:

```bash
git clone <REPOSITORY_URL> cvservice_web
cd cvservice_web
```

Вариант без Git, с локального компьютера:

```powershell
scp -r C:\Study\temp\vadim\bach_teth\cvservice_web yc-user@<PUBLIC_IP>:~/cvservice_web
```

Не передавайте локальный `.env` с секретами. Для продакшена создайте отдельный файл.

## 4. Создать production env

На сервере:

```bash
cd ~/cvservice_web
cp .env.production.example .env.production
nano .env.production
```

Обязательно поменяйте:

- `SECRET_KEY`;
- `POSTGRES_PASSWORD`.

Для `POSTGRES_PASSWORD` используйте латинские буквы и цифры. Если добавить символы вроде `@`, `/`, `:` или `#`, пароль нужно URL-кодировать в строке подключения.

Сгенерировать `SECRET_KEY` можно так:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

## 5. Запустить сайт

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Проверка:

```bash
docker compose -f docker-compose.prod.yml ps
curl http://127.0.0.1/health
```

После этого сайт должен открываться по адресу:

```text
http://<PUBLIC_IP>/
```

## 6. Обновление после изменений

```bash
cd ~/cvservice_web
git pull
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

Если проект передавался через `scp`, повторно скопируйте файлы и выполните команду запуска.

## 7. Полезные команды

Логи приложения:

```bash
docker compose -f docker-compose.prod.yml logs -f web
```

Остановка без удаления данных:

```bash
docker compose -f docker-compose.prod.yml stop
```

Резервная копия PostgreSQL:

```bash
docker exec rezumit_postgres pg_dump -U postgres cvservice_db > rezumit_backup.sql
```

## Замечания

Docker-образ включает TeX Live (`xelatex`, `pdflatex`), кириллические LaTeX-пакеты и шрифт Liberation Serif как Linux fallback для сборки PDF. Поэтому на опубликованном сервере доступны оба варианта экспорта: `.tex` и `.pdf`.

После установки LaTeX-слоя образ становится заметно тяжелее. Для долгой эксплуатации полезно периодически проверять место на диске:

```bash
df -h /
docker system df
```
