# Происхождение и зависимости

`hiring/skills.py` и `hiring/requirements_parser.py` сохраняют и адаптируют словарь навыков и локальный парсер из проекта, предоставленного Вадимом. Команда сообщила о согласии автора и организаторов на использование. Это фиксация сообщённого разрешения, а не публичная лицензия или независимая юридическая оценка.

Другие части старого веб-приложения и экспериментальные сервисы не входят в текущую ветку и контейнер. Их удаление не меняет Git-историю. Runtime зависимости бота зафиксированы в `requirements-hiring.lock`.

Ниже инвентаризация метаданных установленных runtime-пакетов на 27 сентября 2026, не самостоятельное юридическое заключение. Версии зафиксированы в lock; полные тексты лицензий сохраняются в установленных пакетах/их dist-info.

| Лицензия по метаданным | Пакеты |
| --- | --- |
| MIT | annotated-doc, annotated-types, anyio, fastapi, h11, pydantic, pydantic-core, PyJWT, SQLAlchemy, typing-inspection |
| BSD-3-Clause | click, httpcore, httpx, idna, pypdf, python-dotenv, starlette, uvicorn |
| BSD (classifier) | colorama, только Windows |
| MPL-2.0 | certifi |
| ISC | dnspython |
| Unlicense | email-validator |
| MIT AND PSF-2.0 | greenlet |
| PSF-2.0 | typing-extensions |
| Apache-2.0 | python-multipart |

Схема и валидатор DATA-API взяты из указанного организаторами репозитория; происхождение и версии описаны в [DATA-API.md](DATA-API.md). Дополнительный сертификат MAX используется только в MAX HTTP-клиенте; системное хранилище сертификатов не изменяется.

Схема, пример и валидатор DATA-API предоставлены организаторами; источник, версия и лицензия зафиксированы в `tools/data_api/UPSTREAM.md` и `tools/data_api/licence.md`. Эти файлы сохранены без изменений.
