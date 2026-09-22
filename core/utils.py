from __future__ import annotations

from urllib.parse import urlparse

SOFT_SKILL_OPTIONS = [
    {"value": "communication", "label": "Коммуникация"},
    {"value": "teamwork", "label": "Командная работа"},
    {"value": "responsibility", "label": "Ответственность"},
    {"value": "fast learning", "label": "Быстрая обучаемость"},
    {"value": "self organization", "label": "Самоорганизация"},
    {"value": "problem solving", "label": "Решение проблем"},
    {"value": "attention to detail", "label": "Внимательность к деталям"},
    {"value": "adaptability", "label": "Адаптивность"},
    {"value": "leadership", "label": "Лидерство"},
    {"value": "mentoring", "label": "Наставничество"},
    {"value": "time management", "label": "Тайм-менеджмент"},
    {"value": "stakeholder communication", "label": "Работа со стейкхолдерами"},
    {"value": "business communication", "label": "Деловая коммуникация"},
    {"value": "presentation skills", "label": "Презентации"},
    {"value": "negotiation", "label": "Переговоры"},
    {"value": "stakeholder management", "label": "Управление стейкхолдерами"},
    {"value": "client orientation", "label": "Клиентоориентированность"},
    {"value": "clear speech", "label": "Грамотная речь"},
    {"value": "information search", "label": "Поиск информации"},
    {"value": "large information volume", "label": "Работа с большим объёмом информации"},
    {"value": "interest in it", "label": "Интерес к IT-сфере"},
    {"value": "growth mindset", "label": "Желание развиваться и обучаться"},
]
SOFT_SKILL_LABELS = {item["value"]: item["label"] for item in SOFT_SKILL_OPTIONS}

SKILL_DISPLAY_NAMES = {
    "a/b testing": "A/B testing",
    "airflow": "Airflow",
    "api documentation": "API documentation",
    "api specification": "API specification",
    "asp.net core": "ASP.NET Core",
    "asyncio": "asyncio",
    "aws": "AWS",
    "backlog management": "Backlog management",
    "business process modeling": "BPMN / business process modeling",
    "c#": "C#",
    "celery": "Celery",
    "ci/cd": "CI/CD",
    "clickhouse": "ClickHouse",
    "code review": "Code review",
    "css": "CSS",
    "customer development": "CustDev",
    "database schema design": "Проектирование схем БД",
    "devops": "DevOps",
    "docker": "Docker",
    "dotnet": ".NET",
    "dry": "DRY",
    "elasticsearch": "Elasticsearch",
    "entity framework": "Entity Framework",
    "entity framework core": "Entity Framework Core",
    "fastapi": "FastAPI",
    "git": "Git",
    "gitlab ci": "GitLab CI",
    "github actions": "GitHub Actions",
    "go": "Go",
    "google ads": "Google Ads",
    "google cloud": "Google Cloud",
    "grafana": "Grafana",
    "helm": "Helm",
    "html": "HTML",
    "javascript": "JavaScript",
    "jira": "Jira",
    "kafka": "Kafka",
    "kubernetes": "Kubernetes",
    "linux": "Linux",
    "metabase": "Metabase",
    "microservices architecture": "Микросервисная архитектура",
    "mssql": "MS SQL",
    "mysql": "MySQL",
    "node.js": "Node.js",
    "openapi": "OpenAPI",
    "postgresql": "PostgreSQL",
    "power bi": "Power BI",
    "prefect": "Prefect",
    "product analytics": "Product analytics",
    "prometheus": "Prometheus",
    "pytest": "pytest",
    "python": "Python",
    "rabbitmq": "RabbitMQ",
    "raw sql": "Raw SQL",
    "react": "React",
    "redis": "Redis",
    "requirements analysis": "Анализ требований",
    "requirements gathering": "Сбор требований",
    "rest api": "REST API",
    "roadmap": "Roadmap",
    "sanic": "Sanic",
    "sentry": "Sentry",
    "solid": "SOLID",
    "sql": "SQL",
    "sql profiling": "Профилирование SQL",
    "sql query optimization": "Оптимизация SQL-запросов",
    "sqlalchemy": "SQLAlchemy",
    "tableau": "Tableau",
    "technical specification": "Техническое задание",
    "terraform": "Terraform",
    "typescript": "TypeScript",
    "unit testing": "Unit testing",
    "user stories": "User stories",
    "vue": "Vue.js",
    "wildberries": "Wildberries",
    "yandex cloud": "Yandex Cloud",
    "yandex direct": "Яндекс Директ",
}

SKILL_ALIASES = {
    ".net": "dotnet",
    ".net core": "dotnet",
    "net core": "dotnet",
    "asp.net": "dotnet",
    "ms sql": "mssql",
    "microsoft sql server": "mssql",
    "sql server": "mssql",
    "postgres": "postgresql",
    "postgre": "postgresql",
    "js": "javascript",
    "ts": "typescript",
    "react.js": "react",
    "reactjs": "react",
    "vue.js": "vue",
    "vuejs": "vue",
    "vue js": "vue",
    "nodejs": "node.js",
    "node": "node.js",
    "golang": "go",
    "c sharp": "c#",
    "spring boot": "spring",
    "sql alchemy": "sqlalchemy",
    "entity framework core": "entity framework",
    "ef core": "entity framework",
    "rabbit mq": "rabbitmq",
    "apache kafka": "kafka",
    "k8s": "kubernetes",
    "gitlab-ci": "gitlab ci",
    "cicd": "ci/cd",
    "continuous integration": "ci/cd",
    "continuous delivery": "ci/cd",
    "unit tests": "unit testing",
    "unit test": "unit testing",
    "юнит тесты": "unit testing",
    "юнит-тесты": "unit testing",
    "модульные тесты": "unit testing",
    "codereview": "code review",
    "code review": "code review",
    "ревью кода": "code review",
    "сырой sql": "raw sql",
    "сырые sql запросы": "raw sql",
    "сырые запросы": "raw sql",
    "sql profiling": "sql profiling",
    "профилирование sql": "sql profiling",
    "тест кейсы": "test cases",
    "тест-кейсы": "test cases",
    "тестовая документация": "test documentation",
    "баг-репорты": "bug reporting",
    "ручное тестирование": "manual testing",
    "автотесты": "automated testing",
    "автоматизированное тестирование": "automated testing",
    "test automation": "automated testing",
    "сбор требований": "requirements gathering",
    "выявление требований": "requirements gathering",
    "анализ требований": "requirements analysis",
    "пользовательские истории": "user stories",
    "user story": "user stories",
    "управление backlog": "backlog management",
    "управление бэклогом": "backlog management",
    "бэклог": "backlog management",
    "дорожная карта": "roadmap",
    "роадмап": "roadmap",
    "custdev": "customer development",
    "кастдев": "customer development",
    "продуктовая аналитика": "product analytics",
    "ab testing": "a/b testing",
    "a/b тестирование": "a/b testing",
    "ab тестирование": "a/b testing",
    "спецификация api": "api specification",
    "описание api": "api specification",
    "техническое задание": "technical specification",
    "тз": "technical specification",
    "моделирование бизнес-процессов": "business process modeling",
    "business process modelling": "business process modeling",
    "product manager": "product management",
    "продакт": "product management",
    "product owner": "product management",
    "project manager": "project management",
    "проектный менеджмент": "project management",
    "менеджер проектов": "project management",
    "аналитика": "analytics",
    "user experience": "ux",
    "user interface": "ui",
    "яндекс.директ": "yandex direct",
    "яндекс директ": "yandex direct",
    "click house": "clickhouse",
    "elastic search": "elasticsearch",
    "yandex cloud": "yandex cloud",
    "яндекс cloud": "yandex cloud",
    "коммуникация": "communication",
    "коммуникабельность": "communication",
    "общение": "communication",
    "communication": "communication",
    "командная работа": "teamwork",
    "работа в команде": "teamwork",
    "teamwork": "teamwork",
    "ответственность": "responsibility",
    "responsibility": "responsibility",
    "быстрая обучаемость": "fast learning",
    "обучаемость": "fast learning",
    "fast learning": "fast learning",
    "самоорганизация": "self organization",
    "self organization": "self organization",
    "self-organization": "self organization",
    "решение проблем": "problem solving",
    "умение решать проблемы": "problem solving",
    "находить решение к проблемам": "problem solving",
    "находить решение проблем": "problem solving",
    "находить решения проблем": "problem solving",
    "способность находить решение к проблемам": "problem solving",
    "problem solving": "problem solving",
    "внимательность к деталям": "attention to detail",
    "attention to detail": "attention to detail",
    "адаптивность": "adaptability",
    "adaptability": "adaptability",
    "лидерство": "leadership",
    "leadership": "leadership",
    "наставничество": "mentoring",
    "mentoring": "mentoring",
    "тайм-менеджмент": "time management",
    "time management": "time management",
    "работа со стейкхолдерами": "stakeholder communication",
    "взаимодействие со стейкхолдерами": "stakeholder communication",
    "stakeholder communication": "stakeholder communication",
    "деловая коммуникация": "business communication",
    "деловое общение": "business communication",
    "business communication": "business communication",
    "business communications": "business communication",
    "презентации": "presentation skills",
    "делать презентации": "presentation skills",
    "подготовка презентаций": "presentation skills",
    "проводить презентации": "presentation skills",
    "presentation skills": "presentation skills",
    "presentations": "presentation skills",
    "переговоры": "negotiation",
    "ведение переговоров": "negotiation",
    "negotiation": "negotiation",
    "negotiations": "negotiation",
    "управление стейкхолдерами": "stakeholder management",
    "stakeholder management": "stakeholder management",
    "клиентоориентированность": "client orientation",
    "клиент ориентированность": "client orientation",
    "customer focus": "client orientation",
    "client orientation": "client orientation",
    "грамотная речь": "clear speech",
    "clear speech": "clear speech",
    "умение искать и находить необходимую информацию": "information search",
    "поиск информации": "information search",
    "information search": "information search",
    "работа с большим объемом информации": "large information volume",
    "работа с большим объёмом информации": "large information volume",
    "large information volume": "large information volume",
    "интерес к it-сфере": "interest in it",
    "интерес к it": "interest in it",
    "interest in it": "interest in it",
    "желание развиваться и обучаться": "growth mindset",
    "желание развиваться": "growth mindset",
    "growth mindset": "growth mindset",
    "способность осваивать новые технологии": "fast learning",
    "осваивать новые технологии": "fast learning",
    "холодные продажи": "cold sales",
    "холодные звонки": "cold calls",
    "навыки продаж": "sales skills",
    "работа с возражениями": "objection handling",
    "опыт в b2b": "b2b experience",
    "опыт в it": "it experience",
    "опыт в консалтинге": "consulting experience",
    "работа с crm": "crm",
}


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def normalize_optional_url(value: str | None) -> str | None:
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Введите корректный URL, начиная с http:// или https://")

    return normalized


def normalize_skill(value: str) -> str:
    skill = value.strip().lower().replace("ё", "е")
    skill = SKILL_ALIASES.get(skill, skill)
    return skill


def is_soft_skill(value: str) -> bool:
    return normalize_skill(value) in SOFT_SKILL_LABELS


def display_skill_name(value: str) -> str:
    normalized = normalize_skill(value)
    return SKILL_DISPLAY_NAMES.get(normalized) or SOFT_SKILL_LABELS.get(normalized, value)


def parse_skills_input(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []

    parts = []
    for chunk in raw_value.replace("\n", ",").replace(";", ",").split(","):
        normalized = normalize_skill(chunk)
        if normalized:
            parts.append(normalized)

    unique_skills: list[str] = []
    seen: set[str] = set()

    for skill in parts:
        if skill not in seen:
            unique_skills.append(skill)
            seen.add(skill)

    return unique_skills
