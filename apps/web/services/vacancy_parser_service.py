"""
Парсинг текста вакансии по URL через requests + BeautifulSoup4.
"""
from __future__ import annotations

import re

from apps.web.services.llm_service import LlmServiceError, extract_requirements_with_llm
from core.config import settings
from core.utils import normalize_skill


REQUIREMENT_CATEGORIES: dict[str, tuple[str, str]] = {
    "python": ("technology", "Python"),
    "java": ("technology", "Java"),
    "javascript": ("technology", "JavaScript"),
    "typescript": ("technology", "TypeScript"),
    "react": ("technology", "React"),
    "vue": ("technology", "Vue"),
    "angular": ("technology", "Angular"),
    "node.js": ("technology", "Node.js"),
    "async python": ("programming_paradigm", "Асинхронный Python"),
    "asyncio": ("technology", "asyncio"),
    "aiohttp": ("technology", "aiohttp"),
    "fastapi": ("framework", "FastAPI"),
    "sanic": ("framework", "Sanic"),
    "django": ("framework", "Django"),
    "flask": ("framework", "Flask"),
    "spring": ("framework", "Spring"),
    "pydantic": ("technology", "Pydantic"),
    "go": ("technology", "Go"),
    "c#": ("technology", "C#"),
    "dotnet": ("framework", ".NET"),
    "php": ("technology", "PHP"),
    "sql": ("database", "SQL"),
    "postgresql": ("database", "PostgreSQL"),
    "mysql": ("database", "MySQL"),
    "mssql": ("database", "MS SQL"),
    "mongodb": ("database", "MongoDB"),
    "redis": ("database", "Redis"),
    "orm": ("database_practice", "ORM"),
    "sqlalchemy": ("database_practice", "SQLAlchemy"),
    "entity framework": ("database_practice", "Entity Framework"),
    "hibernate": ("database_practice", "Hibernate"),
    "raw sql": ("database_practice", "Сырые SQL-запросы"),
    "sql profiling": ("database_practice", "Профилирование SQL-запросов"),
    "database schema design": ("database_practice", "Проектирование схем БД"),
    "sql optimization": ("database_practice", "Оптимизация SQL-запросов"),
    "rabbitmq": ("message_broker", "RabbitMQ"),
    "kafka": ("message_broker", "Kafka"),
    "celery": ("message_broker", "Celery"),
    "docker": ("infrastructure", "Docker"),
    "kubernetes": ("infrastructure", "Kubernetes"),
    "helm": ("infrastructure", "Helm"),
    "terraform": ("infrastructure", "Terraform"),
    "ansible": ("infrastructure", "Ansible"),
    "nginx": ("infrastructure", "Nginx"),
    "prometheus": ("infrastructure", "Prometheus"),
    "grafana": ("infrastructure", "Grafana"),
    "sentry": ("infrastructure", "Sentry"),
    "linux": ("infrastructure", "Linux"),
    "elasticsearch": ("infrastructure", "Elasticsearch"),
    "clickhouse": ("database", "ClickHouse"),
    "git": ("tool", "Git"),
    "gitlab ci": ("tool", "GitLab CI"),
    "github actions": ("tool", "GitHub Actions"),
    "ci/cd": ("engineering_practice", "CI/CD"),
    "code review": ("engineering_practice", "Code Review"),
    "unit testing": ("engineering_practice", "Unit testing"),
    "manual testing": ("engineering_practice", "Ручное тестирование"),
    "automated testing": ("engineering_practice", "Автоматизированное тестирование"),
    "test cases": ("engineering_practice", "Тест-кейсы"),
    "test documentation": ("engineering_practice", "Тестовая документация"),
    "bug reporting": ("engineering_practice", "Баг-репорты"),
    "pytest": ("technology", "pytest"),
    "jest": ("technology", "Jest"),
    "vitest": ("technology", "Vitest"),
    "cypress": ("technology", "Cypress"),
    "selenium": ("technology", "Selenium"),
    "playwright": ("technology", "Playwright"),
    "rest api": ("api", "REST API"),
    "openapi": ("api", "OpenAPI"),
    "graphql": ("api", "GraphQL"),
    "api specification": ("api", "Спецификация API"),
    "microservices architecture": ("architecture", "Микросервисная архитектура"),
    "service design": ("architecture", "Проектирование сервисов"),
    "devops": ("engineering_practice", "DevOps"),
    "html": ("frontend", "HTML"),
    "css": ("frontend", "CSS"),
    "tailwind": ("frontend", "Tailwind CSS"),
    "figma": ("tool", "Figma"),
    "jira": ("tool", "Jira"),
    "confluence": ("tool", "Confluence"),
    "agile": ("methodology", "Agile"),
    "scrum": ("methodology", "Scrum"),
    "kanban": ("methodology", "Kanban"),
    "qa": ("engineering_practice", "QA"),
    "requirements analysis": ("analysis_practice", "Анализ требований"),
    "requirements gathering": ("analysis_practice", "Сбор требований"),
    "user stories": ("analysis_practice", "User stories"),
    "backlog management": ("management_practice", "Управление backlog"),
    "roadmap": ("management_practice", "Roadmap"),
    "customer development": ("product_practice", "Customer development"),
    "product analytics": ("product_practice", "Продуктовая аналитика"),
    "a/b testing": ("product_practice", "A/B-тестирование"),
    "bpmn": ("analysis_practice", "BPMN"),
    "uml": ("analysis_practice", "UML"),
    "technical specification": ("analysis_practice", "Техническое задание"),
    "business process modeling": ("analysis_practice", "Моделирование бизнес-процессов"),
    "product management": ("product_practice", "Product management"),
    "project management": ("management_practice", "Project management"),
    "analytics": ("analysis_practice", "Analytics"),
    "ux": ("design", "UX"),
    "ui": ("design", "UI"),
    "wildberries": ("marketplace", "Wildberries"),
    "ozon": ("marketplace", "Ozon"),
    "yandex direct": ("ads_api", "Яндекс.Директ"),
    "google ads": ("ads_api", "Google Ads"),
    "tableau": ("bi_tool", "Tableau"),
    "power bi": ("bi_tool", "Power BI"),
    "metabase": ("bi_tool", "Metabase"),
    "aws": ("cloud", "AWS"),
    "google cloud": ("cloud", "Google Cloud"),
    "yandex cloud": ("cloud", "Yandex Cloud"),
    "airflow": ("orchestration", "Airflow"),
    "prefect": ("orchestration", "Prefect"),
    "dry": ("principle", "DRY"),
    "kiss": ("principle", "KISS"),
    "solid": ("principle", "SOLID"),
    "communication": ("soft_skill", "Коммуникация"),
    "teamwork": ("soft_skill", "Командная работа"),
    "responsibility": ("soft_skill", "Ответственность"),
    "fast learning": ("soft_skill", "Быстрая обучаемость"),
    "self organization": ("soft_skill", "Самоорганизация"),
    "problem solving": ("soft_skill", "Решение проблем"),
    "attention to detail": ("soft_skill", "Внимательность к деталям"),
    "adaptability": ("soft_skill", "Адаптивность"),
    "leadership": ("soft_skill", "Лидерство"),
    "mentoring": ("soft_skill", "Наставничество"),
    "time management": ("soft_skill", "Тайм-менеджмент"),
    "stakeholder communication": ("soft_skill", "Работа со стейкхолдерами"),
    "business communication": ("soft_skill", "Деловая коммуникация"),
    "presentation skills": ("soft_skill", "Презентации"),
    "negotiation": ("soft_skill", "Переговоры"),
    "stakeholder management": ("soft_skill", "Управление стейкхолдерами"),
    "client orientation": ("soft_skill", "Клиентоориентированность"),
    "clear speech": ("soft_skill", "Грамотная речь"),
    "information search": ("soft_skill", "Поиск информации"),
    "large information volume": ("soft_skill", "Работа с большим объёмом информации"),
    "interest in it": ("soft_skill", "Интерес к IT-сфере"),
    "growth mindset": ("soft_skill", "Желание развиваться и обучаться"),
    "sales skills": ("sales_skill", "Навыки продаж"),
    "cold sales": ("sales_skill", "Холодные продажи"),
    "cold calls": ("sales_skill", "Холодные звонки"),
    "objection handling": ("sales_skill", "Работа с возражениями"),
    "b2b experience": ("domain_experience", "Опыт в B2B"),
    "it experience": ("domain_experience", "Опыт в IT"),
    "consulting experience": ("domain_experience", "Опыт в консалтинге"),
    "crm": ("tool", "CRM"),
}


KNOWN_SKILL_ALIASES: dict[str, list[str]] = {
    "python": ["python", "python 3", "python3", "питон"],
    "java": ["java"],
    "javascript": ["javascript", "js", "джаваскрипт"],
    "typescript": ["typescript", "ts"],
    "react": ["react", "react.js", "reactjs"],
    "vue": ["vue", "vue.js"],
    "angular": ["angular"],
    "node.js": ["node.js", "nodejs", "node"],
    "async python": ["асинхронном python", "асинхронный python", "async python"],
    "asyncio": ["asyncio", "асинхронного программирования"],
    "aiohttp": ["aiohttp"],
    "fastapi": ["fastapi"],
    "sanic": ["sanic"],
    "django": ["django"],
    "flask": ["flask"],
    "pydantic": ["pydantic"],
    "spring": ["spring", "spring boot"],
    "go": ["golang", "go "],
    "c#": ["c#", "c sharp"],
    "dotnet": [".net", "dotnet", "asp.net"],
    "php": ["php"],
    "sql": ["sql"],
    "postgresql": ["postgresql", "postgres"],
    "mysql": ["mysql"],
    "mssql": ["mssql", "ms sql", "sql server"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "orm": ["orm", "object-relational mapping"],
    "sqlalchemy": ["sqlalchemy", "sql alchemy"],
    "entity framework": ["entity framework", "entity framework core", "ef core"],
    "hibernate": ["hibernate"],
    "raw sql": ["сырых запросов", "сырые запросы", "raw sql", "raw-запросы"],
    "sql profiling": ["профилированием", "профилирование sql", "sql profiling"],
    "rabbitmq": ["rabbitmq", "rabbit mq"],
    "kafka": ["kafka", "apache kafka"],
    "celery": ["celery"],
    "docker": ["docker"],
    "kubernetes": ["kubernetes", "k8s"],
    "helm": ["helm"],
    "terraform": ["terraform"],
    "ansible": ["ansible"],
    "nginx": ["nginx"],
    "prometheus": ["prometheus"],
    "grafana": ["grafana"],
    "sentry": ["sentry"],
    "git": ["git"],
    "linux": ["linux"],
    "elasticsearch": ["elasticsearch", "elastic search"],
    "clickhouse": ["clickhouse", "click house"],
    "gitlab ci": ["gitlab ci", "gitlab-ci"],
    "github actions": ["github actions"],
    "ci/cd": ["ci/cd", "cicd", "continuous integration", "continuous delivery"],
    "rest api": ["rest", "rest api", "api"],
    "openapi": ["openapi", "api documentation", "документирования rest api"],
    "graphql": ["graphql"],
    "html": ["html"],
    "css": ["css"],
    "tailwind": ["tailwind"],
    "figma": ["figma"],
    "jira": ["jira"],
    "confluence": ["confluence"],
    "agile": ["agile"],
    "scrum": ["scrum"],
    "kanban": ["kanban"],
    "qa": ["qa", "тестирование"],
    "selenium": ["selenium"],
    "playwright": ["playwright"],
    "jest": ["jest"],
    "vitest": ["vitest"],
    "cypress": ["cypress"],
    "pytest": ["pytest"],
    "manual testing": ["ручное тестирование", "manual testing"],
    "automated testing": ["автоматизированное тестирование", "автотесты", "automated testing", "test automation"],
    "test cases": ["тест-кейсы", "тест кейсы", "test cases"],
    "test documentation": ["тестовая документация", "test documentation"],
    "bug reporting": ["баг-репорты", "bug reports", "bug reporting"],
    "unit testing": ["unit testing", "unit tests", "unit тесты", "unit тестов", "юнит тесты", "юнит-тесты", "модульные тесты"],
    "code review": ["codereview", "code review", "code-review", "ревью кода"],
    "product management": ["product manager", "product management", "продакт"],
    "project management": ["project manager", "project management", "проектный менеджмент"],
    "analytics": ["analytics", "аналитика"],
    "requirements analysis": ["анализ требований", "requirements analysis"],
    "requirements gathering": ["сбор требований", "выявление требований", "requirements gathering"],
    "user stories": ["user stories", "user story", "пользовательские истории"],
    "backlog management": ["backlog", "управление backlog", "управление бэклогом", "бэклог"],
    "roadmap": ["roadmap", "роадмап", "дорожная карта"],
    "customer development": ["customer development", "custdev", "кастдев"],
    "product analytics": ["product analytics", "продуктовая аналитика"],
    "a/b testing": ["a/b testing", "ab testing", "a/b тестирование", "ab тестирование"],
    "bpmn": ["bpmn"],
    "uml": ["uml"],
    "api specification": ["api specification", "спецификация api", "описание api"],
    "technical specification": ["техническое задание", "тз", "technical specification"],
    "business process modeling": ["моделирование бизнес-процессов", "business process modeling", "business process modelling"],
    "ux": ["ux", "user experience"],
    "ui": ["ui", "user interface"],
    "database schema design": ["проектирования схем бд", "схем бд", "database schema design"],
    "sql optimization": ["оптимизации sql", "sql optimization", "оптимизация sql"],
    "dry": ["dry"],
    "kiss": ["kiss"],
    "solid": ["solid"],
    "wildberries": ["wildberries"],
    "ozon": ["ozon"],
    "yandex direct": ["яндекс.директ", "яндекс директ", "yandex direct"],
    "google ads": ["google ads"],
    "tableau": ["tableau"],
    "power bi": ["power bi"],
    "metabase": ["metabase"],
    "aws": ["aws"],
    "google cloud": ["google cloud"],
    "yandex cloud": ["yandex cloud", "яндекс cloud", "yandex cloud"],
    "airflow": ["airflow"],
    "prefect": ["prefect"],
    "microservices architecture": [
        "микросервисной архитектуры",
        "микросервисной архитектурой",
        "микросервисная архитектура",
        "микросервисную архитектуру",
        "microservices architecture",
    ],
    "service design": ["проектировать новые сервисы", "проектирование сервисов", "service design"],
    "devops": ["devops", "devops-подходов", "devops подходов"],
    "communication": ["коммуникация", "коммуникабельность", "communication", "грамотная коммуникация"],
    "teamwork": ["командная работа", "работа в команде", "умение работать в команде", "teamwork", "team player"],
    "responsibility": ["ответственность", "ответственный подход", "responsibility"],
    "fast learning": [
        "быстрая обучаемость",
        "обучаемость",
        "готовность учиться",
        "fast learning",
        "способность осваивать новые технологии",
        "осваивать новые технологии",
    ],
    "self organization": ["самоорганизация", "самостоятельность", "self organization", "self-organization"],
    "problem solving": [
        "решение проблем",
        "problem solving",
        "умение решать проблемы",
        "находить решение к проблемам",
        "находить решение проблем",
    ],
    "attention to detail": ["внимательность к деталям", "attention to detail", "внимательность"],
    "adaptability": ["адаптивность", "гибкость", "adaptability"],
    "leadership": ["лидерство", "leadership"],
    "mentoring": ["наставничество", "менторство", "mentoring"],
    "time management": ["тайм-менеджмент", "time management", "управление временем"],
    "stakeholder communication": ["работа со стейкхолдерами", "stakeholder communication", "взаимодействие со стейкхолдерами"],
    "business communication": ["деловая коммуникация", "деловое общение", "business communication", "business communications"],
    "presentation skills": ["презентации", "делать презентации", "подготовка презентаций", "проводить презентации", "presentation skills", "presentations"],
    "negotiation": ["переговоры", "ведение переговоров", "negotiation", "negotiations"],
    "stakeholder management": ["управление стейкхолдерами", "stakeholder management"],
    "client orientation": ["клиентоориентированность", "клиент ориентированность", "customer focus", "client orientation"],
    "clear speech": ["грамотная речь", "clear speech"],
    "information search": ["умение искать и находить необходимую информацию", "поиск информации", "information search"],
    "large information volume": ["работа с большим объемом информации", "работа с большим объёмом информации", "large information volume"],
    "interest in it": ["интерес к it-сфере", "интерес к it", "interest in it"],
    "growth mindset": ["желание развиваться и обучаться", "желание развиваться", "growth mindset"],
    "sales skills": ["навыки продаж", "sales skills"],
    "cold sales": ["холодные продажи", "cold sales"],
    "cold calls": ["холодные звонки", "cold calls"],
    "objection handling": ["работа с возражениями", "objection handling"],
    "b2b experience": ["опыт в b2b", "b2b experience"],
    "it experience": ["опыт в it", "it experience"],
    "consulting experience": ["опыт в консалтинге", "consulting experience"],
    "crm": ["работа с crm", "crm"],
}

NEUTRAL_STACK_MARKERS = (
    "наш стек",
    "стек:",
    "технологический стек",
    "используемый стек",
    "ключевые навыки:",
)

MUST_MARKERS = (
    "требования",
    "обязательно",
    "необходимо",
    "требуется",
    "уверенное знание",
    "опыт работы с",
    "must",
    "required",
    "requirements",
    "мы ожидаем",
    "мы ожидаем, что вы",
    "ожидаем, что вы",
)
NICE_MARKERS = (
    "желательно",
    "будет плюсом",
    "будут плюсом",
    "будет большим плюсом",
    "будет огромным плюсом",
    "плюсом будет",
    "плюсом будут",
    "огромным +",
    "большим +",
    "+ будут",
    "+ будет",
    "преимуществом будет",
    "будет преимуществом",
    "nice",
    "nice to have",
    "plus",
    "optional",
    "preferred",
    "приветствуется",
)

MUST_SECTION_MARKERS = (
    "требования:",
    "обязательные требования:",
    "необходимые навыки:",
    "мы ожидаем:",
    "мы ожидаем, что вы:",
    "ожидаем, что вы:",
)
NICE_SECTION_MARKERS = NICE_MARKERS

ENGINEERING_ROLE_MARKERS = (
    "backend",
    "back-end",
    "бэкенд",
    "бекенд",
    "frontend",
    "front-end",
    "фронтенд",
    "fullstack",
    "full-stack",
    "devops",
    "sre",
    "site reliability",
    "qa",
    "tester",
    "тестировщик",
    "разработчик",
    "developer",
    "software engineer",
    "инженер",
    "программист",
    "mobile developer",
    "android developer",
    "ios developer",
    "data engineer",
    "ml engineer",
    "machine learning engineer",
)
PEOPLE_ORIENTED_ROLE_MARKERS = (
    "project manager",
    "project-manager",
    "проектный менеджер",
    "менеджер проектов",
    "руководитель проекта",
    "проджект",
    "product manager",
    "product owner",
    "продуктовый менеджер",
    "продакт",
    "scrum master",
    "delivery manager",
    "account manager",
    "customer success",
    "business analyst",
    "бизнес-аналитик",
    "system analyst",
    "системный аналитик",
    "аналитик требований",
    "team lead",
    "тимлид",
    "tech lead",
    "техлид",
    "руководитель команды",
)
HYBRID_ROLE_MARKERS = (
    "business analyst",
    "бизнес-аналитик",
    "system analyst",
    "системный аналитик",
    "аналитик требований",
    "team lead",
    "тимлид",
    "tech lead",
    "техлид",
)
ENGINEERING_CONTEXT_MARKERS = (
    "api",
    "backend",
    "frontend",
    "код",
    "разработка",
    "интеграции",
    "база данных",
    "sql",
    "архитектура",
    "микросервис",
    "инфраструктура",
    "docker",
    "kubernetes",
    "ci/cd",
    "тестирование",
    "автотест",
    "devops",
)
PEOPLE_CONTEXT_MARKERS = (
    "стейкхолдер",
    "заказчик",
    "клиент",
    "сбор требований",
    "анализ требований",
    "интервью",
    "презентации",
    "презентация",
    "переговоры",
    "фасилитация",
    "коммуникация",
    "коммуникации",
    "roadmap",
    "backlog",
    "user story",
    "custdev",
    "customer development",
    "приоритизация",
    "приоритизации",
    "бизнес-процесс",
)
TITLE_LINE_PREFIXES = (
    "вакансия",
    "название",
    "позиция",
    "должность",
    "роль",
)
ROLE_SENSITIVE_CATEGORIES = {"soft_skill", "sales_skill"}


def _contains_alias(text: str, alias: str) -> bool:
    alias = alias.strip().lower()
    if not alias:
        return False
    pattern = rf"(?<![a-zа-я0-9]){re.escape(alias)}(?![a-zа-я0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _marker_count(text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if _contains_alias(text, marker))


def _vacancy_title_context(raw_text: str) -> str:
    first_lines: list[str] = []
    title_lines: list[str] = []
    for line in raw_text.splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue

        if len(first_lines) < 6:
            first_lines.append(clean_line)

        lowered = clean_line.lower()
        if any(lowered.startswith(prefix) for prefix in TITLE_LINE_PREFIXES):
            title_lines.append(clean_line)

        if len(first_lines) >= 6 and title_lines:
            break

    return "\n".join(title_lines or first_lines).lower()


def _vacancy_role_family(raw_text: str) -> str:
    title_context = _vacancy_title_context(raw_text)
    people_title_score = _marker_count(title_context, PEOPLE_ORIENTED_ROLE_MARKERS)
    engineering_title_score = _marker_count(title_context, ENGINEERING_ROLE_MARKERS)
    hybrid_title_score = _marker_count(title_context, HYBRID_ROLE_MARKERS)

    early_context = raw_text[:2400].lower()
    people_context_score = (
        _marker_count(early_context, PEOPLE_ORIENTED_ROLE_MARKERS)
        + _marker_count(early_context, PEOPLE_CONTEXT_MARKERS)
    )
    engineering_context_score = (
        _marker_count(early_context, ENGINEERING_ROLE_MARKERS)
        + _marker_count(early_context, ENGINEERING_CONTEXT_MARKERS)
    )

    if engineering_title_score > people_title_score:
        return "engineering"

    if people_title_score > engineering_title_score:
        if hybrid_title_score and engineering_context_score and people_context_score:
            return "mixed"
        return "people"

    if people_title_score and engineering_title_score:
        return "mixed"

    if people_context_score and engineering_context_score:
        if abs(people_context_score - engineering_context_score) <= 1:
            return "mixed"
        return "people" if people_context_score > engineering_context_score else "engineering"

    if people_context_score:
        return "people"
    if engineering_context_score:
        return "engineering"
    return "unknown"


def apply_soft_skill_role_policy(raw_text: str, requirements: list[dict]) -> list[dict]:
    role_family = _vacancy_role_family(raw_text)
    normalized_requirements: list[dict] = []
    for item in requirements:
        normalized_item = _normalize_requirement_item(item)
        if normalized_item is None:
            continue

        if normalized_item["category"] in ROLE_SENSITIVE_CATEGORIES:
            if role_family == "engineering":
                normalized_item["type"] = "nice"
            elif role_family == "people" and normalized_item["type"] != "nice":
                normalized_item["type"] = "must"
        normalized_requirements.append(normalized_item)

    return deduplicate_requirements(normalized_requirements)


def deduplicate_requirements(requirements: list[dict]) -> list[dict]:
    result_by_skill: dict[str, dict] = {}
    order: list[str] = []

    for item in requirements:
        normalized_item = _normalize_requirement_item(item)
        if normalized_item is None:
            continue

        skill_norm = normalized_item["skill_norm"]
        if skill_norm not in result_by_skill:
            result_by_skill[skill_norm] = normalized_item
            order.append(skill_norm)
            continue

        if _requirement_rank(normalized_item) > _requirement_rank(result_by_skill[skill_norm]):
            result_by_skill[skill_norm] = normalized_item

    return [result_by_skill[skill_norm] for skill_norm in order]


def _normalize_requirement_item(item: dict) -> dict | None:
    original_skill_norm = str(item.get("skill_norm", "")).strip().lower()
    skill_norm = normalize_skill(original_skill_norm)
    if not skill_norm:
        return None

    inferred_category = _category_for_skill(skill_norm)
    category = item.get("category") or inferred_category
    if category == "other" and inferred_category != "other":
        category = inferred_category

    display_name = item.get("display_name")
    if inferred_category != "other" and (
        not display_name
        or str(display_name).strip().lower() in {skill_norm, original_skill_norm}
    ):
        display_name = _display_name_for_skill(skill_norm)

    raw_type = item.get("type", "must")
    if hasattr(raw_type, "value"):
        raw_type = raw_type.value
    requirement_type = "nice" if str(raw_type).strip().lower() == "nice" else "must"

    confidence = item.get("confidence", 0.8)
    try:
        confidence = max(0.0, min(float(confidence), 1.0))
    except (TypeError, ValueError):
        confidence = 0.8

    return {
        "skill_norm": skill_norm,
        "display_name": display_name or _display_name_for_skill(skill_norm),
        "category": category,
        "type": requirement_type,
        "source_text": item.get("source_text"),
        "confidence": confidence,
    }


def _requirement_rank(item: dict) -> tuple[int, int, float, int]:
    source_text = str(item.get("source_text") or "").strip().lower()
    is_generic_key_skill_source = source_text.startswith("ключевые навыки:")
    category = item.get("category") or "other"
    return (
        0 if is_generic_key_skill_source else 1,
        1 if category != "other" else 0,
        float(item.get("confidence") or 0.0),
        1 if source_text else 0,
    )


def _category_for_skill(skill_norm: str) -> str:
    return REQUIREMENT_CATEGORIES.get(skill_norm, ("other", skill_norm))[0]


def _display_name_for_skill(skill_norm: str) -> str:
    return REQUIREMENT_CATEGORIES.get(skill_norm, ("other", skill_norm))[1]


def _compact_source_text(value: str, *, max_length: int = 180) -> str:
    compacted = re.sub(r"\s+", " ", value).strip()
    if len(compacted) <= max_length:
        return compacted
    return compacted[: max_length - 1].rstrip(" .,;:") + "…"


def _source_excerpt(text: str, position: int, *, radius: int = 90) -> str:
    start = max(0, _context_start_before(text, position))
    end_candidates = [idx for idx in (text.find("\n", position), text.find(";", position)) if idx != -1]
    end = min(end_candidates) if end_candidates else min(len(text), position + radius)
    return _compact_source_text(text[start:end])


def _is_negated_mention(text: str, position: int) -> bool:
    prefix = text[max(0, position - 32):position].lower()
    return any(marker in prefix for marker in ("без ", "без использования", "without ", "no ", "not "))


def _nearest_marker_type(text: str, position: int) -> str | None:
    prefix = text[:position].lower()
    last_nice = _last_marker_index(prefix, NICE_SECTION_MARKERS)
    last_must = _last_marker_index(prefix, MUST_SECTION_MARKERS)
    last_neutral = _last_marker_index(prefix, NEUTRAL_STACK_MARKERS)

    nearest = max(last_nice, last_must, last_neutral)
    if nearest < 0:
        return None
    if nearest == last_nice:
        return "nice"
    if nearest == last_must:
        return "must"
    return "neutral"


def _guess_requirement_type(text: str, position: int) -> str | None:
    nearest_marker_type = _nearest_marker_type(text, position)
    if nearest_marker_type in {"nice", "must"}:
        return nearest_marker_type
    if nearest_marker_type == "neutral":
        return None

    context_start = _context_start_before(text, position)
    prefix = text[context_start:position].lower()
    nice_score = sum(marker in prefix for marker in NICE_MARKERS)
    must_score = sum(marker in prefix for marker in MUST_MARKERS)
    if nice_score > must_score:
        return "nice"
    if must_score > nice_score:
        return "must"

    has_any_explicit_section = any(
        marker in text[:position].lower()
        for marker in (*MUST_SECTION_MARKERS, *NICE_SECTION_MARKERS, *NEUTRAL_STACK_MARKERS)
    )
    if has_any_explicit_section:
        return None
    return "must"


def _last_marker_index(text: str, markers: tuple[str, ...]) -> int:
    return max((text.rfind(marker) for marker in markers), default=-1)


def _context_start_before(text: str, position: int) -> int:
    boundaries = ("\n", "\r", ";", "•")
    return max(text.rfind(boundary, 0, position) for boundary in boundaries) + 1


def _find_skill_positions(text: str, skill_norm: str) -> list[int]:
    aliases = KNOWN_SKILL_ALIASES.get(skill_norm, [skill_norm])
    positions: list[int] = []
    for alias in aliases:
        alias = alias.strip().lower()
        if not alias:
            continue
        pattern = rf"(?<![a-zа-я0-9]){re.escape(alias)}(?![a-zа-я0-9])"
        positions.extend(match.start() for match in re.finditer(pattern, text, flags=re.IGNORECASE))
    return sorted(set(positions))


def _find_skill_position(text: str, skill_norm: str) -> int | None:
    positions = _find_skill_positions(text, skill_norm)
    return min(positions) if positions else None


def _non_negated_positions(text: str, skill_norm: str) -> list[int]:
    return [position for position in _find_skill_positions(text, skill_norm) if not _is_negated_mention(text, position)]


def _infer_requirement_type_from_text(text: str, skill_norm: str) -> str | None:
    positions = _non_negated_positions(text, skill_norm)
    if not positions:
        return None

    inferred_types = [_guess_requirement_type(text, position) for position in positions]
    if "must" in inferred_types:
        return "must"
    if "nice" in inferred_types:
        return "nice"
    return None


def _normalize_extracted_requirements(raw_text: str, requirements: list[dict]) -> list[dict]:
    normalized_text = raw_text.lower()
    result_by_skill: dict[str, dict] = {}

    for item in requirements:
        skill_norm = normalize_skill(str(item.get("skill_norm", "")))
        if not skill_norm:
            continue
        if _find_skill_positions(normalized_text, skill_norm) and not _non_negated_positions(normalized_text, skill_norm):
            continue

        raw_type = str(item.get("type", "must")).strip().lower()
        requirement_type = "nice" if raw_type == "nice" else "must"
        inferred_type = _infer_requirement_type_from_text(normalized_text, skill_norm)
        if inferred_type:
            requirement_type = inferred_type

        source_text = str(item.get("source_text") or "").strip()
        positions = _non_negated_positions(normalized_text, skill_norm)
        if not source_text and positions:
            source_text = _source_excerpt(raw_text, positions[0])

        confidence = item.get("confidence", 0.8)
        try:
            confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            confidence = 0.8

        normalized_item = {
            "skill_norm": skill_norm,
            "display_name": item.get("display_name") or _display_name_for_skill(skill_norm),
            "category": item.get("category") or _category_for_skill(skill_norm),
            "type": requirement_type,
            "source_text": source_text or None,
            "confidence": confidence,
        }

        # If the same competency appears twice, keep must as the stronger requirement.
        if result_by_skill.get(skill_norm, {}).get("type") == "must":
            continue
        if requirement_type == "must" or skill_norm not in result_by_skill:
            result_by_skill[skill_norm] = normalized_item

    return list(result_by_skill.values())


def _extract_hh_key_skill_requirements(raw_text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for line in raw_text.splitlines():
        if not line.lower().startswith("ключевые навыки:"):
            continue

        _, _, skills_text = line.partition(":")
        for raw_skill in skills_text.split(","):
            skill_norm = normalize_skill(raw_skill)
            if skill_norm:
                result.append(
                    {
                        "skill_norm": skill_norm,
                        "display_name": _display_name_for_skill(skill_norm),
                        "category": _category_for_skill(skill_norm),
                        "type": "must",
                        "source_text": line.strip(),
                        "confidence": 0.7,
                    }
                )
        break
    return result


def _merge_requirements(
    primary: list[dict],
    secondary: list[dict],
) -> list[dict]:
    result: list[dict] = []

    for source in (primary, secondary):
        for item in source:
            skill_norm = normalize_skill(str(item.get("skill_norm", "")))
            if not skill_norm:
                continue
            requirement_type = "nice" if item.get("type") == "nice" else "must"
            result.append(
                {
                    "skill_norm": skill_norm,
                    "display_name": item.get("display_name") or _display_name_for_skill(skill_norm),
                    "category": item.get("category") or _category_for_skill(skill_norm),
                    "type": requirement_type,
                    "source_text": item.get("source_text"),
                    "confidence": item.get("confidence", 0.8),
                }
            )

    return deduplicate_requirements(result)


def extract_requirements_locally(raw_text: str) -> list[dict]:
    normalized_text = raw_text.lower()
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for skill, aliases in KNOWN_SKILL_ALIASES.items():
        positions = _non_negated_positions(normalized_text, skill)
        if not positions:
            continue

        requirement_type = _infer_requirement_type_from_text(normalized_text, skill) or _guess_requirement_type(
            normalized_text,
            min(positions),
        )
        if requirement_type is None:
            continue
        skill_norm = normalize_skill(skill)
        key = (requirement_type, skill_norm)
        if key in seen:
            continue
        result.append(
            {
                "skill_norm": skill_norm,
                "display_name": _display_name_for_skill(skill_norm),
                "category": _category_for_skill(skill_norm),
                "type": requirement_type,
                "source_text": _source_excerpt(raw_text, positions[0]),
                "confidence": 0.85,
            }
        )
        seen.add(key)

    return result


def _build_role_context(raw_text: str, vacancy_title: str | None = None) -> str:
    title = (vacancy_title or "").strip()
    if not title:
        return raw_text
    return f"Вакансия: {title}\n{raw_text}"


def extract_vacancy_requirements(raw_text: str, *, vacancy_title: str | None = None) -> list[dict]:
    role_context = _build_role_context(raw_text, vacancy_title)
    hh_key_skills = _extract_hh_key_skill_requirements(raw_text)

    if settings.deepseek_api_key:
        try:
            llm_requirements = extract_requirements_with_llm(raw_text)
            if llm_requirements:
                normalized = _normalize_extracted_requirements(raw_text, llm_requirements)
                return apply_soft_skill_role_policy(role_context, _merge_requirements(normalized, hh_key_skills))
        except LlmServiceError:
            pass

    local_requirements = _normalize_extracted_requirements(raw_text, extract_requirements_locally(raw_text))
    return apply_soft_skill_role_policy(role_context, _merge_requirements(local_requirements, hh_key_skills))
