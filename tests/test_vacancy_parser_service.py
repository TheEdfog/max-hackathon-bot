from apps.web.services import vacancy_parser_service
from apps.web.services.vacancy_parser_service import apply_soft_skill_role_policy, extract_vacancy_requirements


def test_hh_key_skills_are_kept_when_llm_skips_them(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Вакансия: Backend-разработчик
Ключевые навыки: C#, MySQL, React, .NET Core

Описание вакансии:
Требования: уверенное знание C# и MS SQL.
Огромным плюсом будут знания Vue.js и JavaScript.
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item["type"] for item in requirements}

    assert by_skill["c#"] == "must"
    assert by_skill["mysql"] == "must"
    assert by_skill["react"] == "must"
    assert by_skill["dotnet"] == "must"
    assert by_skill["vue"] == "nice"
    assert by_skill["javascript"] == "nice"


def test_plus_section_overrides_neutral_stack_section(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Наш стек:
Python 3.10+
FastAPI
PostgreSQL
Redis
Docker / Docker Compose
Linux
Git
REST API / OpenAPI
Async stack: asyncio, aiohttp
Pydantic

Требования:
Уверенное знание Python 3
Опыт разработки на FastAPI
Понимание асинхронного программирования (asyncio)
Опыт работы с PostgreSQL
Опыт контейнеризации приложений (Docker)
Базовые навыки администрирования Linux
Уверенная работа с Git

Будет плюсом:
Redis
pytest
CI/CD
опыт микросервисной архитектуры
понимание DevOps-подходов
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item["type"] for item in requirements}

    assert by_skill["python"] == "must"
    assert by_skill["fastapi"] == "must"
    assert by_skill["postgresql"] == "must"
    assert by_skill["docker"] == "must"
    assert by_skill["redis"] == "nice"
    assert by_skill["pytest"] == "nice"
    assert by_skill["ci/cd"] == "nice"
    assert by_skill["microservices architecture"] == "nice"
    assert by_skill["devops"] == "nice"


def test_extracts_engineering_competencies_and_skips_negative_orm(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Мы ожидаем, что вы:
прекрасно ориентируетесь в асинхронном Python 3.9+ (фреймворки Sanic и FastApi);
отлично знаете и умеете работать с PostgreSQL (в том числе знакомы с профилированием и написанием сырых запросов без ORM);
имеете опыт использования docker, k8s, prometheus, grafana, sentry;
знакомы с микросервисной архитектурой и умеете проектировать новые сервисы с нуля;
имеете опыт проведения CodeReview;
пишите много unit тестов (мы используем pytest).
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item for item in requirements}

    expected_must = {
        "async python",
        "python",
        "sanic",
        "fastapi",
        "postgresql",
        "sql profiling",
        "raw sql",
        "docker",
        "kubernetes",
        "prometheus",
        "grafana",
        "sentry",
        "microservices architecture",
        "service design",
        "code review",
        "unit testing",
        "pytest",
    }

    assert expected_must.issubset(by_skill)
    assert all(by_skill[skill]["type"] == "must" for skill in expected_must)
    assert "orm" not in by_skill
    assert by_skill["unit testing"]["category"] == "engineering_practice"
    assert by_skill["code review"]["display_name"] == "Code Review"


def test_soft_skills_are_nice_for_engineering_roles(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Вакансия: Backend-разработчик

Требования:
Уверенное знание Python
Грамотная коммуникация
Ответственность
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item for item in requirements}

    assert by_skill["python"]["type"] == "must"
    assert by_skill["communication"]["type"] == "nice"
    assert by_skill["responsibility"]["type"] == "nice"
    assert by_skill["communication"]["category"] == "soft_skill"


def test_soft_skills_are_must_for_people_oriented_roles(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Вакансия: Project Manager / менеджер проектов

Требования:
Деловая коммуникация
Презентации для заказчиков
Работа со стейкхолдерами
Ответственность
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item for item in requirements}

    assert by_skill["business communication"]["type"] == "must"
    assert by_skill["presentation skills"]["type"] == "must"
    assert by_skill["stakeholder communication"]["type"] == "must"
    assert by_skill["responsibility"]["type"] == "must"
    assert by_skill["presentation skills"]["category"] == "soft_skill"


def test_old_other_soft_skills_are_recategorized_for_engineering_roles():
    raw_text = "Вакансия: Backend-разработчик (Junior)\nТребования: Python, грамотная речь, холодные продажи."
    requirements = [
        {
            "skill_norm": "грамотная речь",
            "display_name": "грамотная речь",
            "category": "other",
            "type": "must",
        },
        {
            "skill_norm": "холодные продажи",
            "display_name": "холодные продажи",
            "category": "other",
            "type": "must",
        },
    ]

    updated = apply_soft_skill_role_policy(raw_text, requirements)
    by_skill = {item["skill_norm"]: item for item in updated}

    assert by_skill["clear speech"]["category"] == "soft_skill"
    assert by_skill["clear speech"]["type"] == "nice"
    assert by_skill["cold sales"]["category"] == "sales_skill"
    assert by_skill["cold sales"]["type"] == "nice"


def test_engineering_title_makes_soft_skills_nice_even_under_requirements(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Требования:

Навыки: C#, .NET, MS SQL, огромным + будут знания: VUE.JS, JavaScript;
Интерес к IT-сфере, желание развиваться и обучаться;
Умение искать и находить необходимую информацию;
Способность осваивать новые технологии и находить решение к проблемам;
Умение работать в команде;
Образование высшее( возможно неоконченное).
"""

    requirements = extract_vacancy_requirements(raw_text, vacancy_title="Backend-разработчик (Junior)")
    by_skill = {item["skill_norm"]: item for item in requirements}

    assert by_skill["c#"]["type"] == "must"
    assert by_skill["dotnet"]["type"] == "must"
    assert by_skill["mssql"]["type"] == "must"
    assert by_skill["vue"]["type"] == "nice"
    assert by_skill["javascript"]["type"] == "nice"
    assert by_skill["interest in it"]["type"] == "nice"
    assert by_skill["growth mindset"]["type"] == "nice"
    assert by_skill["information search"]["type"] == "nice"
    assert by_skill["fast learning"]["type"] == "nice"
    assert by_skill["problem solving"]["type"] == "nice"
    assert by_skill["teamwork"]["type"] == "nice"


def test_expanded_dictionary_extracts_tooling_and_analysis_practices(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Vacancy: Backend Developer

Requirements:
Experience with RabbitMQ, Kafka, Terraform, GitLab CI, SQLAlchemy.
Experience writing Cypress/Jest tests, test cases and API specification.
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item for item in requirements}

    expected_must = {
        "rabbitmq",
        "kafka",
        "terraform",
        "gitlab ci",
        "sqlalchemy",
        "cypress",
        "jest",
        "test cases",
        "api specification",
    }

    assert expected_must.issubset(by_skill)
    assert all(by_skill[skill]["type"] == "must" for skill in expected_must)
    assert by_skill["rabbitmq"]["category"] == "message_broker"
    assert by_skill["api specification"]["category"] == "api"


def test_people_role_keeps_explicit_plus_soft_skill_as_nice(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Vacancy: Product Manager

Requirements:
Backlog management and user stories.

Nice to have:
Presentation skills and stakeholder communication.
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item for item in requirements}

    assert by_skill["backlog management"]["type"] == "must"
    assert by_skill["user stories"]["type"] == "must"
    assert by_skill["presentation skills"]["type"] == "nice"
    assert by_skill["stakeholder communication"]["type"] == "nice"


def test_mixed_business_analyst_context_preserves_explicit_soft_skill_type(monkeypatch):
    monkeypatch.setattr(vacancy_parser_service.settings, "deepseek_api_key", "")

    raw_text = """
Vacancy: Business Analyst

Requirements:
SQL, API specification and requirements gathering.

Will be a plus:
Presentation skills and stakeholder communication.
"""

    requirements = extract_vacancy_requirements(raw_text)
    by_skill = {item["skill_norm"]: item for item in requirements}

    assert by_skill["sql"]["type"] == "must"
    assert by_skill["api specification"]["type"] == "must"
    assert by_skill["requirements gathering"]["type"] == "must"
    assert by_skill["presentation skills"]["type"] == "nice"
    assert by_skill["stakeholder communication"]["type"] == "nice"


def test_duplicate_requirements_are_collapsed_after_normalization():
    raw_text = """
Вакансия: Менеджер по первичным продажам в ИТ / SDR

Требования:
Клиентоориентированность, грамотная речь, холодные продажи.
"""
    requirements = [
        {
            "skill_norm": "клиентоориентированность",
            "display_name": "клиентоориентированность",
            "category": "other",
            "type": "must",
            "confidence": 0.4,
        },
        {
            "skill_norm": "client orientation",
            "display_name": "Клиентоориентированность",
            "category": "soft_skill",
            "type": "must",
            "confidence": 0.9,
        },
        {
            "skill_norm": "холодные продажи",
            "display_name": "холодные продажи",
            "category": "other",
            "type": "must",
            "confidence": 0.4,
        },
        {
            "skill_norm": "cold sales",
            "display_name": "Холодные продажи",
            "category": "sales_skill",
            "type": "must",
            "confidence": 0.9,
        },
    ]

    updated = apply_soft_skill_role_policy(raw_text, requirements)
    by_skill = {item["skill_norm"]: item for item in updated}

    assert list(by_skill) == ["client orientation", "cold sales"]
    assert by_skill["client orientation"]["category"] == "soft_skill"
    assert by_skill["cold sales"]["category"] == "sales_skill"
