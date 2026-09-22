from __future__ import annotations

from openai import OpenAI

from core.config import settings


class LlmServiceError(Exception):
    pass


def _get_client() -> OpenAI:
    if not settings.deepseek_api_key:
        raise LlmServiceError(
            "Не настроен модуль генерации резюме."
        )

    return OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def _extract_chat_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    parts: list[str] = []
    for item in content or []:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", "")
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def extract_vacancy_text_from_url(source_url: str) -> str:
    client = _get_client()

    system_prompt = (
        "Ты извлекаешь текст вакансии по URL. "
        "Верни только содержимое вакансии: требования, обязанности, условия. "
        "Убери навигацию сайта, рекламу и служебный мусор. "
        "Если текст недоступен, верни ровно: CANNOT_EXTRACT."
    )

    user_prompt = f"URL вакансии: {source_url}"

    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        raise LlmServiceError("Интеллектуальный модуль временно недоступен.") from exc

    extracted = _extract_chat_text(response)
    if not extracted or extracted == "CANNOT_EXTRACT":
        raise LlmServiceError("Система не смогла извлечь текст вакансии по указанной ссылке.")

    return extracted


# Ниже находятся актуальные реализации. Они переопределяют ранние варианты выше,
# чтобы пользовательские ошибки и промпты были читаемыми в UTF-8.
import json
import re
from datetime import datetime
from typing import Any


def _get_client() -> OpenAI:
    if not settings.deepseek_api_key:
        raise LlmServiceError(
            "Не настроен модуль генерации резюме."
        )

    return OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def _extract_chat_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    parts: list[str] = []
    for item in content or []:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", "")
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _extract_reasoning_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    reasoning = getattr(message, "reasoning_content", "")
    return reasoning.strip() if isinstance(reasoning, str) else ""


def _log_llm_exception(exc: Exception) -> None:
    try:
        settings.ensure_runtime_directories()
        log_path = settings.temp_path / "llm_errors.log"
        lines = [
            f"[{datetime.now().isoformat(timespec='seconds')}] {type(exc).__name__}: {exc!r}",
        ]
        cause = getattr(exc, "__cause__", None)
        while cause is not None:
            lines.append(f"CAUSE {type(cause).__name__}: {cause!r}")
            cause = getattr(cause, "__cause__", None)
        log_path.open("a", encoding="utf-8").write("\n".join(lines) + "\n")
    except Exception:
        pass


def _chat(system_prompt: str, user_prompt: str, *, temperature: float = 0) -> str:
    client = _get_client()
    try:
        request_payload = {
            "model": settings.llm_model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if settings.llm_max_tokens > 0:
            request_payload["max_tokens"] = settings.llm_max_tokens

        response = client.chat.completions.create(**request_payload)
    except Exception as exc:  # noqa: BLE001
        _log_llm_exception(exc)
        raise LlmServiceError("Интеллектуальный модуль временно недоступен.") from exc

    text = _extract_chat_text(response)
    if text:
        return text

    if _extract_reasoning_text(response):
        raise LlmServiceError(
            "Интеллектуальный модуль не успел сформировать финальный ответ. Попробуйте повторить генерацию."
        )

    raise LlmServiceError("Интеллектуальный модуль вернул пустой ответ. Попробуйте повторить генерацию.")


def _json_from_text(text: str) -> Any:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    first_bracket = min(
        [idx for idx in [cleaned.find("["), cleaned.find("{")] if idx != -1],
        default=0,
    )
    cleaned = cleaned[first_bracket:].strip()
    return json.loads(cleaned)


def extract_vacancy_text_from_url(source_url: str) -> str:
    system_prompt = (
        "Ты извлекаешь текст вакансии по URL. Верни только содержимое вакансии: название, "
        "обязанности, требования, желательные навыки, условия. Убери навигацию сайта, рекламу "
        "и служебный мусор. Если текст недоступен, верни ровно CANNOT_EXTRACT."
    )
    extracted = _chat(system_prompt, f"URL вакансии: {source_url}")
    if not extracted or extracted == "CANNOT_EXTRACT":
        raise LlmServiceError("Система не смогла извлечь текст вакансии по указанной ссылке.")
    return extracted


def extract_requirements_with_llm(raw_text: str) -> list[dict]:
    system_prompt = (
        "Ты анализируешь IT-вакансию и извлекаешь требования/компетенции для ATS/match-скоринга. "
        "Верни только JSON-массив. Каждый элемент: "
        '{"skill_norm":"нормализованный ключ на нижнем регистре",'
        '"display_name":"человекочитаемое название",'
        '"category":"technology|framework|programming_paradigm|database|database_practice|message_broker|infrastructure|tool|frontend|design|api|architecture|engineering_practice|analysis_practice|management_practice|product_practice|methodology|marketplace|ads_api|bi_tool|cloud|orchestration|principle|soft_skill|sales_skill|domain_experience|other",'
        '"type":"must|nice","source_text":"короткий фрагмент вакансии-основание","confidence":0.0}. '
        "must = обязательное требование, без которого кандидат хуже подходит. "
        "nice = желательное требование или плюс, который повышает релевантность, но не является обязательным. "
        "Извлекай не только технологии, но и инженерные практики: unit testing, manual testing, automated testing, code review, raw sql, sql profiling, service design, microservices architecture. "
        "Извлекай инструменты и практики анализа/продукта, если они прямо есть в вакансии: Jira, Confluence, BPMN, UML, requirements analysis, requirements gathering, user stories, backlog management, roadmap, customer development, product analytics, A/B testing, API specification, technical specification. "
        "Извлекай инфраструктурные и backend-компетенции, если они указаны: RabbitMQ, Kafka, Celery, Nginx, Elasticsearch, ClickHouse, Terraform, Ansible, Helm, GitLab CI, GitHub Actions, SQLAlchemy, Entity Framework, Hibernate. "
        "Если указаны маркетплейсы, рекламные API, BI, облака или оркестраторы, извлекай их как отдельные компетенции: Wildberries, Ozon, Yandex Direct, Google Ads, Tableau, Power BI, Metabase, AWS, Google Cloud, Yandex Cloud, Airflow, Prefect. "
        "Если вакансия явно просит soft skills, извлекай их как category=soft_skill: communication, teamwork, responsibility, fast learning, self organization, problem solving, attention to detail, adaptability, leadership, mentoring, time management, stakeholder communication, business communication, presentation skills, negotiation, stakeholder management, client orientation, clear speech, information search, large information volume, interest in it, growth mindset. "
        "Навыки продаж и клиентской работы извлекай отдельно: sales skills, cold sales, cold calls, objection handling = category=sales_skill; b2b experience, it experience, consulting experience = category=domain_experience; crm = category=tool. "
        "Не назначай must/nice заранее по названию навыка или по одной профессии: основной источник обязательности — формулировка вакансии и маркеры разделов. "
        "Контекст роли используй только для спорных soft_skill/sales_skill. Для инженерных ролей (backend, frontend, fullstack, devops, QA, developer, engineer) soft_skill обычно nice, даже если он стоит в общем блоке требований. "
        "Для people/managerial ролей (project manager, product manager, product owner, business/system analyst, scrum master, delivery manager, team lead) soft_skill может быть must, если текст вакансии просит его как рабочую компетенцию: коммуникация, презентации, переговоры, фасилитация и работа со стейкхолдерами для таких ролей часто являются профессиональными навыками. "
        "Если название роли и обязанности смешанные или противоречат друг другу, например Business Analyst с продуктовой логикой или техническими задачами, опирайся на обязанности и явные маркеры must/nice, а не только на название. "
        "Нормализуй варианты: k8s -> kubernetes, FastApi -> fastapi, CodeReview -> code review, unit тесты/юнит-тесты -> unit testing, GitLab CI/GitHub Actions -> соответствующий tool, ORM-библиотеки -> SQLAlchemy/Entity Framework/Hibernate. "
        "Если фрагмент содержит отрицание или запрет, не извлекай это как требование. Например: «без ORM» не означает требование ORM; "
        "смысл такого фрагмента лучше извлечь как raw sql, если речь о сырых SQL-запросах. "
        "Если навык указан после маркеров «желательно», «будет плюсом», «будут плюсом», "
        "«плюсом будет», «приветствуется», «optional», «nice to have», «preferred», "
        "«+ будут знания», то классифицируй его как nice, даже если весь пункт находится в разделе «Требования». "
        "Если один пункт смешанный, раздели его по смыслу. Например: "
        "«Навыки: C#, .NET, MS SQL, огромным + будут знания: VUE.JS, JavaScript» -> "
        "C#, .NET, MS SQL = must; Vue.js, JavaScript = nice. "
        "Не добавляй требования, которых нет в вакансии. Для каждого требования обязательно укажи source_text."
    )
    user_prompt = f"Текст вакансии:\n{raw_text[:12000]}"
    text = _chat(system_prompt, user_prompt)

    try:
        data = _json_from_text(text)
    except Exception as exc:  # noqa: BLE001
        raise LlmServiceError("Система вернула требования в некорректном формате.") from exc

    if not isinstance(data, list):
        raise LlmServiceError("Система должна вернуть список требований.")

    result: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        skill_norm = str(item.get("skill_norm", "")).strip().lower()
        requirement_type = str(item.get("type", "must")).strip().lower()
        if not skill_norm:
            continue
        confidence = item.get("confidence", 0.8)
        try:
            confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            confidence = 0.8
        result.append(
            {
                "skill_norm": skill_norm,
                "display_name": str(item.get("display_name") or skill_norm).strip(),
                "category": str(item.get("category") or "other").strip(),
                "type": "nice" if requirement_type == "nice" else "must",
                "source_text": str(item.get("source_text") or "").strip(),
                "confidence": confidence,
            }
        )
    return result


def generate_resume_with_llm(payload: dict) -> dict:
    system_prompt = (
        "Ты эксперт по адаптации резюме под IT-вакансии с учетом ATS. "
        "Нельзя выдумывать опыт, проекты, навыки, метрики и факты. "
        "Верни только JSON по структуре, которую просит пользователь."
    )
    user_prompt = (
        "Сформируй адаптированное резюме. Используй только исходные факты. "
        "Структура JSON: contact, target_position, summary, skills, experience, projects, "
        "education, certificates, languages, ats_notes.\n\n"
        f"Данные:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.2)
    data = _json_from_text(text)
    if not isinstance(data, dict):
        raise LlmServiceError("Система должна вернуть структуру резюме.")
    return data


def revise_resume_with_llm(resume_json: dict, instruction: str) -> dict:
    system_prompt = (
        "Ты редактируешь уже сгенерированное резюме по замечанию пользователя. "
        "Нельзя добавлять новые факты, которых нет в исходном JSON. "
        "Верни только обновленный JSON без комментариев."
    )
    user_prompt = (
        f"Замечание пользователя:\n{instruction}\n\n"
        f"Текущее резюме JSON:\n{json.dumps(resume_json, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.2)
    data = _json_from_text(text)
    if not isinstance(data, dict):
        raise LlmServiceError("Система должна вернуть структуру резюме.")
    return data
def generate_resume_with_llm(payload: dict) -> dict:
    generation_options = payload.get("generation_options") or {}
    metrics_mode = generation_options.get("metrics_mode", "strict")
    vacancy_title = ((payload.get("vacancy") or {}).get("title") or "").strip()
    low_match = bool(generation_options.get("low_match"))
    if metrics_mode == "assist":
        metrics_instruction = (
            "Пользователь включил режим помощи с метриками. Разрешено осторожно предлагать приблизительные "
            "временные или финансовые метрики только на основе связки описанных задач и достижений/результатов. "
            "Если пользователь описал достижения, считай их главным источником для оценки метрик: смотри, "
            "что именно было улучшено, автоматизировано, ускорено, запущено или доведено до результата. "
            "Метрики должны быть реалистичными, умеренными и не должны противоречить входным данным. "
            "Если оценить метрику нельзя, не добавляй цифру."
        )
        achievement_hint = "1-3 пункта; можно осторожно добавить реалистичные оценочные метрики, если они выводятся из задач и достижений пользователя"
    else:
        metrics_instruction = (
            "Пользователь выбрал строгий режим. Запрещено добавлять новые метрики, проценты, суммы и сроки, "
            "если их нет во входных данных."
        )
        achievement_hint = "1-3 пункта, без выдуманных цифр"

    system_prompt = (
        "Ты карьерный консультант и редактор IT-резюме для русскоязычного рынка. "
        "Собери готовое резюме под вакансию: живое, аккуратное, ATS-friendly, без таблиц и без декоративного шума. "
        "Главный источник фактов - профиль кандидата и поле profile_text. Вакансия, match_result и рекомендации "
        "нужны для отбора релевантного материала, а не для придумывания нового опыта. "
        "Строго запрещено выдумывать опыт, навыки, проекты, даты, компании и достижения. "
        "Поле target_position должно точно совпадать с названием вакансии из входных данных; "
        "не заменяй его целевой должностью из профиля кандидата. "
        f"{metrics_instruction} "
        "Можно только переформулировать, отбирать и структурировать факты, которые есть во входных данных. "
        "Верни только валидный JSON без markdown и без пояснений."
    )
    schema_hint = {
        "contact": {
            "full_name": "string",
            "email": "string",
            "phone": "string",
            "city": "string|null",
            "work_format": "string|null",
            "github_url": "string|null",
            "linkedin_url": "string|null",
        },
        "target_position": "string",
        "summary": ["2-4 коротких предложения о кандидате под вакансию"],
        "skills": {
            "relevant": ["hard skills из профиля, релевантные вакансии"],
            "other": ["другие подтвержденные hard skills"],
            "soft": ["до 3 soft skills, если они есть в профиле"],
        },
        "experience": [
            {
                "company": "string",
                "city": "string|null",
                "period": "string",
                "position": "string",
                "tasks": ["до 6 пунктов"],
                "achievements": [achievement_hint],
            }
        ],
        "projects": [
            {
                "name": "string",
                "stack": ["string"],
                "role": "string",
                "description": "string",
                "result": "string",
            }
        ],
        "education": ["string"],
        "certificates": ["string"],
        "languages": ["string"],
        "ats_notes": {
            "llm_status": "deepseek",
            "generation_rule": "string",
        },
    }
    user_prompt = (
        "Сформируй финальное резюме под вакансию. "
        f"Поле target_position заполни строго так: «{vacancy_title}». "
        "Не используй в шапке старую целевую должность из профиля, даже если match низкий. "
        "Ключевые навыки из вакансии добавляй только если они подтверждены в профиле. "
        "Профиль может содержать весь рабочий и учебный опыт кандидата; в итоговое резюме отбирай только то, "
        "что помогает вакансии, ATS и живому рекрутеру. Нерелевантные навыки, проекты и детали можно опустить. "
        "Если match низкий, не пытайся сделать резюме визуально полным ценой нерелевантного опыта: "
        "лучше оставить experience, projects, certificates или summary пустыми, чем переносить туда чуждый вакансии материал. "
        "Для низкого match используй только прямо подтвержденные релевантные навыки и факты; "
        "раздел skills.other лучше оставлять пустым. "
        "Soft skills выбирай по такой логике: если вакансия явно просит soft skills, бери только те из них, "
        "которые есть в профиле кандидата; если вакансия не просит soft skills явно, добавь не больше 2-3 "
        "самых уместных качеств из профиля. Если качество не подтверждается контекстом опыта/проекта, "
        "лучше не превращать его в сильное утверждение и не выдумывать историю. "
        "Раздел «О себе» должен быть профессиональным: не включай личные увлечения и бытовые факты, "
        "если они не помогают трудоустройству. "
        "Опыт и проекты перепиши как резюме, а не как анкету: действие + технология + результат, где это возможно. "
        "Если у места работы в профиле указан город или удаленный формат, сохрани его в поле city. "
        "Если данных мало, не компенсируй это выдумкой.\n\n"
        f"Низкий match: {'да' if low_match else 'нет'}.\n\n"
        f"Ожидаемая JSON-структура:\n{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        f"Входные данные:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.15)
    data = _json_from_text(text)
    if not isinstance(data, dict):
        raise LlmServiceError("Система должна вернуть структуру резюме.")

    ats_notes = data.setdefault("ats_notes", {})
    if isinstance(ats_notes, dict):
        ats_notes["llm_status"] = "deepseek"
        ats_notes["metrics_mode"] = metrics_mode
        ats_notes.setdefault("generation_rule", "Резюме собрано только из фактов профиля пользователя.")
    return data


def revise_resume_with_llm(resume_json: dict, instruction: str) -> dict:
    system_prompt = (
        "Ты редактируешь готовое IT-резюме по замечанию пользователя. "
        "Нельзя добавлять новые факты, которых нет в текущем JSON. "
        "Сохрани структуру JSON резюме. Верни только валидный JSON без markdown."
    )
    user_prompt = (
        f"Замечание пользователя:\n{instruction}\n\n"
        f"Текущее резюме JSON:\n{json.dumps(resume_json, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.15)
    data = _json_from_text(text)
    if not isinstance(data, dict):
        raise LlmServiceError("Система должна вернуть структуру резюме.")

    ats_notes = data.setdefault("ats_notes", {})
    if isinstance(ats_notes, dict):
        ats_notes["llm_status"] = "deepseek_revision"
    return data


def generate_cover_letter_with_llm(payload: dict) -> dict:
    system_prompt = (
        "Ты карьерный консультант и редактор сопроводительных писем для IT-вакансий. "
        "Собери короткое, живое и деловое сопроводительное письмо на русском языке. "
        "Главный источник фактов - профиль кандидата и profile_text; вакансия, match_result и рекомендации "
        "нужны для отбора релевантных аргументов. Нельзя выдумывать опыт, навыки, проекты, даты, компании, "
        "личные обстоятельства и достижения. Письмо не должно дублировать резюме полностью. "
        "Оптимальный объем: 250-400 слов, 3-6 коротких абзацев, читается с экрана за 1-2 минуты. "
        "Структура: приветствие, позиция/интерес, 2-3 сильных аргумента кандидата, мотивация, призыв к контакту. "
        "Если название компании неизвестно, не выдумывай его и используй нейтральное обращение. "
        "Верни только валидный JSON без markdown и без пояснений."
    )
    schema_hint = {
        "candidate_name": "string",
        "vacancy_title": "string",
        "subject": "string",
        "greeting": "string",
        "paragraphs": [
            "3-6 коротких абзацев сопроводительного письма, без буллетов и без воды"
        ],
        "closing": "string",
        "notes": {
            "llm_status": "deepseek",
            "generation_rule": "string",
            "word_count_target": "250-400",
        },
    }
    user_prompt = (
        "Сформируй сопроводительное письмо под вакансию. "
        "Выбери из профиля только то, что усиливает отклик на эту вакансию. "
        "Покажи мотивацию и ценность кандидата, но не превращай письмо в пересказ всего резюме. "
        "Если match высокий, сделай акцент на релевантном опыте и быстрой применимости. "
        "Если есть пробелы, не оправдывайся и не выдумывай опыт; лучше подчеркни подтвержденные сильные стороны "
        "и готовность быстро закрывать недостающие зоны. "
        "Не добавляй факты, которых нет во входных данных.\n\n"
        f"Ожидаемая JSON-структура:\n{json.dumps(schema_hint, ensure_ascii=False)}\n\n"
        f"Входные данные:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.2)
    data = _json_from_text(text)
    if not isinstance(data, dict):
        raise LlmServiceError("Система должна вернуть структуру сопроводительного письма.")

    notes = data.setdefault("notes", {})
    if isinstance(notes, dict):
        notes["llm_status"] = "deepseek"
        notes.setdefault("generation_rule", "Письмо собрано только из фактов профиля пользователя и текста вакансии.")
        notes.setdefault("word_count_target", "250-400")
    return data


def revise_cover_letter_with_llm(letter_json: dict, instruction: str) -> dict:
    system_prompt = (
        "Ты редактируешь готовое сопроводительное письмо по замечанию пользователя. "
        "Нельзя добавлять новые факты, которых нет в текущем JSON. "
        "Сохрани формат делового письма, объем 250-400 слов и структуру JSON. "
        "Верни только валидный JSON без markdown."
    )
    user_prompt = (
        f"Замечание пользователя:\n{instruction}\n\n"
        f"Текущее сопроводительное письмо JSON:\n{json.dumps(letter_json, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.15)
    data = _json_from_text(text)
    if not isinstance(data, dict):
        raise LlmServiceError("Система должна вернуть структуру сопроводительного письма.")

    notes = data.setdefault("notes", {})
    if isinstance(notes, dict):
        notes["llm_status"] = "deepseek_revision"
    return data


def improve_recommendation_actions_with_llm(payload: dict) -> list[dict]:
    system_prompt = (
        "Ты карьерный консультант для IT-кандидатов. Твоя задача - переписать только поле action "
        "в рекомендациях так, чтобы совет был конкретным, уместным для типа требования и не звучал шаблонно. "
        "Не меняй смысл рекомендации, класс, impact, effort и based_on. "
        "Не предлагай добавлять навык или качество в резюме, если его нельзя честно подтвердить. "
        "Для технологий давай практический совет: что изучить, какой мини-проект или задачу сделать, как подтвердить опыт. "
        "Для soft skills и мотивационных требований вроде «интерес к IT», «желание развиваться», «готовность учиться» "
        "не пиши «найдите ситуацию, где это качество проявить» механически. Лучше предложи показать это через траекторию обучения, "
        "выбранные проекты, регулярную практику, обратную связь или осознанную мотивацию. "
        "Верни только JSON-массив объектов вида {\"index\":0,\"action\":\"короткий совет 1-2 предложения\"}."
    )
    user_prompt = (
        "Перепиши actions для рекомендаций. Вакансия и исходные данные нужны только для контекста, факты не выдумывай.\n\n"
        f"Данные:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    text = _chat(system_prompt, user_prompt, temperature=0.25)
    data = _json_from_text(text)
    if not isinstance(data, list):
        raise LlmServiceError("Система должна вернуть список уточненных действий.")
    return [item for item in data if isinstance(item, dict)]
