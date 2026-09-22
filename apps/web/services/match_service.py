"""
Расчёт must_pct, nice_pct и total_pct.
"""
from __future__ import annotations

import re
from typing import Any

from apps.api.db.models import Profile, RequirementType, Vacancy, VacancyRequirement
from core.utils import normalize_skill


LOW_MATCH_WARNING_THRESHOLD = 35.0


RELATED_SKILLS: dict[str, set[str]] = {
    "mssql": {"sql"},
    "mysql": {"sql"},
    "postgresql": {"sql"},
    "sql": {"mssql", "mysql", "postgresql"},
    "dotnet": {".net", ".net core", "asp.net"},
    "vue": {"vue.js"},
    "react": {"react.js", "reactjs"},
    "javascript": {"js"},
    "typescript": {"ts"},
    "kubernetes": {"k8s"},
    "ci/cd": {"gitlab ci", "github actions"},
    "gitlab ci": {"ci/cd"},
    "github actions": {"ci/cd"},
    "unit testing": {"pytest", "jest", "vitest"},
    "pytest": {"unit testing", "automated testing"},
    "jest": {"unit testing", "automated testing"},
    "vitest": {"unit testing", "automated testing"},
    "cypress": {"automated testing"},
    "selenium": {"automated testing"},
    "playwright": {"automated testing"},
    "automated testing": {"unit testing", "pytest", "jest", "vitest", "cypress", "selenium", "playwright"},
    "raw sql": {"sql", "sql optimization", "sql profiling"},
    "sql profiling": {"sql", "raw sql", "sql optimization"},
    "sql optimization": {"sql", "raw sql", "sql profiling"},
    "orm": {"sqlalchemy", "entity framework", "hibernate"},
    "sqlalchemy": {"orm"},
    "entity framework": {"orm"},
    "hibernate": {"orm"},
    "code review": {"codereview"},
    "requirements analysis": {"requirements gathering", "user stories"},
    "requirements gathering": {"requirements analysis", "user stories"},
    "user stories": {"requirements analysis", "requirements gathering", "backlog management"},
    "backlog management": {"user stories", "roadmap"},
    "bpmn": {"business process modeling", "requirements analysis"},
    "uml": {"requirements analysis"},
}


TEXT_MATCH_ALIASES: dict[str, set[str]] = {
    "unit testing": {"unit testing", "unit tests", "unit тесты", "unit тестов", "юнит тесты", "юнит-тесты", "модульные тесты"},
    "code review": {"code review", "codereview", "code-review", "ревью кода"},
    "kubernetes": {"kubernetes", "k8s"},
    "fastapi": {"fastapi", "fast api"},
    "raw sql": {"raw sql", "сырые запросы", "сырых запросов", "raw-запросы"},
    "sql profiling": {"sql profiling", "профилирование sql", "профилированием"},
    "sql optimization": {"sql optimization", "оптимизация sql", "оптимизации sql", "оптимизация sql-запросов"},
    "orm": {"orm"},
    "sqlalchemy": {"sqlalchemy", "sql alchemy"},
    "entity framework": {"entity framework", "entity framework core", "ef core"},
    "hibernate": {"hibernate"},
    "rabbitmq": {"rabbitmq", "rabbit mq"},
    "kafka": {"kafka", "apache kafka"},
    "celery": {"celery"},
    "nginx": {"nginx"},
    "terraform": {"terraform"},
    "ansible": {"ansible"},
    "helm": {"helm"},
    "gitlab ci": {"gitlab ci", "gitlab-ci"},
    "github actions": {"github actions"},
    "ci/cd": {"ci/cd", "cicd", "continuous integration", "continuous delivery"},
    "jest": {"jest"},
    "vitest": {"vitest"},
    "cypress": {"cypress"},
    "selenium": {"selenium"},
    "playwright": {"playwright"},
    "manual testing": {"manual testing", "ручное тестирование"},
    "automated testing": {"automated testing", "test automation", "автотесты", "автоматизированное тестирование"},
    "test cases": {"test cases", "тест-кейсы", "тест кейсы"},
    "test documentation": {"test documentation", "тестовая документация"},
    "bug reporting": {"bug reporting", "bug reports", "баг-репорты"},
    "requirements analysis": {"requirements analysis", "анализ требований"},
    "requirements gathering": {"requirements gathering", "сбор требований", "выявление требований"},
    "user stories": {"user stories", "user story", "пользовательские истории"},
    "backlog management": {"backlog", "управление backlog", "управление бэклогом", "бэклог"},
    "roadmap": {"roadmap", "роадмап", "дорожная карта"},
    "customer development": {"customer development", "custdev", "кастдев"},
    "product analytics": {"product analytics", "продуктовая аналитика"},
    "a/b testing": {"a/b testing", "ab testing", "a/b тестирование", "ab тестирование"},
    "bpmn": {"bpmn"},
    "uml": {"uml"},
    "api specification": {"api specification", "спецификация api", "описание api"},
    "technical specification": {"technical specification", "техническое задание", "тз"},
    "business process modeling": {"business process modeling", "business process modelling", "моделирование бизнес-процессов"},
    "microservices architecture": {
        "microservices architecture",
        "микросервисная архитектура",
        "микросервисной архитектуры",
        "микросервисной архитектурой",
        "микросервисную архитектуру",
    },
    "communication": {"communication", "коммуникация", "коммуникабельность", "грамотная коммуникация"},
    "teamwork": {"teamwork", "team player", "командная работа", "работа в команде"},
    "responsibility": {"responsibility", "ответственность", "ответственный подход"},
    "fast learning": {"fast learning", "быстрая обучаемость", "обучаемость", "готовность учиться"},
    "self organization": {"self organization", "self-organization", "самоорганизация", "самостоятельность"},
    "problem solving": {"problem solving", "решение проблем", "умение решать проблемы"},
    "attention to detail": {"attention to detail", "внимательность к деталям", "внимательность"},
    "adaptability": {"adaptability", "адаптивность", "гибкость"},
    "leadership": {"leadership", "лидерство"},
    "mentoring": {"mentoring", "наставничество", "менторство"},
    "time management": {"time management", "тайм-менеджмент", "управление временем"},
    "stakeholder communication": {
        "stakeholder communication",
        "работа со стейкхолдерами",
        "взаимодействие со стейкхолдерами",
    },
    "business communication": {"business communication", "business communications", "деловая коммуникация", "деловое общение"},
    "presentation skills": {
        "presentation skills",
        "presentations",
        "презентации",
        "делать презентации",
        "подготовка презентаций",
        "проводить презентации",
    },
    "negotiation": {"negotiation", "negotiations", "переговоры", "ведение переговоров"},
    "stakeholder management": {"stakeholder management", "управление стейкхолдерами"},
    "client orientation": {"client orientation", "customer focus", "клиентоориентированность", "клиент ориентированность"},
    "clear speech": {"clear speech", "грамотная речь"},
    "information search": {"information search", "поиск информации", "умение искать и находить необходимую информацию"},
    "large information volume": {
        "large information volume",
        "работа с большим объемом информации",
        "работа с большим объёмом информации",
    },
    "interest in it": {"interest in it", "интерес к it", "интерес к it-сфере"},
    "growth mindset": {"growth mindset", "желание развиваться", "желание развиваться и обучаться"},
    "sales skills": {"sales skills", "навыки продаж"},
    "cold sales": {"cold sales", "холодные продажи"},
    "cold calls": {"cold calls", "холодные звонки"},
    "objection handling": {"objection handling", "работа с возражениями"},
    "b2b experience": {"b2b experience", "опыт в b2b"},
    "it experience": {"it experience", "опыт в it"},
    "consulting experience": {"consulting experience", "опыт в консалтинге"},
    "crm": {"crm", "работа с crm"},
}


def _compact_text(value: str | None, *, max_length: int = 180) -> str | None:
    if not value:
        return None
    compacted = re.sub(r"\s+", " ", value).strip()
    if len(compacted) <= max_length:
        return compacted
    return compacted[: max_length - 1].rstrip(" .,;:") + "…"


def _normalized_text(value: str | None) -> str:
    return (value or "").lower()


def _count_skill_hits(skill: str, text: str) -> int:
    if not skill:
        return 0
    aliases = {skill.lower(), *TEXT_MATCH_ALIASES.get(normalize_skill(skill), set())}
    total = 0
    for alias in aliases:
        pattern = rf"(?<![a-zа-я0-9]){re.escape(alias.lower())}(?![a-zа-я0-9])"
        total += len(re.findall(pattern, text, flags=re.IGNORECASE))
    return total


def _profile_text(profile: Profile) -> str:
    parts = [
        profile.target_position,
        profile.summary_text,
        getattr(profile, "experience_text", None),
        getattr(profile, "projects_text", None),
        getattr(profile, "education_text", None),
        getattr(profile, "certificates_text", None),
        getattr(profile, "languages_text", None),
        getattr(profile, "soft_skills_text", None),
        " ".join(skill.skill_norm for skill in profile.skills),
    ]
    return "\n".join(part for part in parts if part).lower()


def _context_score(skill: str, profile: Profile) -> float:
    experience_hits = _count_skill_hits(skill, _normalized_text(getattr(profile, "experience_text", None)))
    project_hits = _count_skill_hits(skill, _normalized_text(getattr(profile, "projects_text", None)))
    summary_hits = _count_skill_hits(skill, _normalized_text(profile.summary_text))
    certificates_hits = _count_skill_hits(skill, _normalized_text(getattr(profile, "certificates_text", None)))

    if experience_hits or project_hits:
        return 1.0
    if certificates_hits:
        return 0.75
    if summary_hits:
        return 0.5
    return 0.0


def _related_skills(skill: str) -> set[str]:
    normalized_skill = normalize_skill(skill)
    related = {normalize_skill(item) for item in RELATED_SKILLS.get(normalized_skill, set())}
    return {item for item in related if item and item != normalized_skill}


def _related_skill_match(skill: str, candidate_skills: set[str]) -> tuple[float, list[str]]:
    hits = sorted(_related_skills(skill).intersection(candidate_skills))
    return (1.0 if hits else 0.0), hits


def _related_text_hits(skill: str, profile_blob: str) -> tuple[int, list[str]]:
    hits: list[str] = []
    total = 0
    for related_skill in sorted(_related_skills(skill)):
        count = _count_skill_hits(related_skill, profile_blob)
        if count:
            hits.append(related_skill)
            total += count
    return total, hits


def _requirement_type_value(requirement: VacancyRequirement) -> str:
    raw_value = requirement.type.value if isinstance(requirement.type, RequirementType) else str(requirement.type)
    return RequirementType.NICE.value if raw_value == RequirementType.NICE.value else RequirementType.MUST.value


def _requirement_confidence(requirement: VacancyRequirement) -> float:
    try:
        return max(0.0, min(float(requirement.confidence or 0.0), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _requirement_rank(requirement: VacancyRequirement) -> tuple[int, int, float, int]:
    source_text = str(requirement.source_text or "").strip().lower()
    is_generic_key_skill_source = source_text.startswith("ключевые навыки:")
    category = requirement.category or "other"
    return (
        0 if is_generic_key_skill_source else 1,
        1 if category != "other" else 0,
        _requirement_confidence(requirement),
        1 if source_text else 0,
    )


def _deduplicate_vacancy_requirements(requirements: list[VacancyRequirement]) -> list[VacancyRequirement]:
    selected_by_skill: dict[str, VacancyRequirement] = {}
    order: list[str] = []

    for requirement in requirements:
        skill_norm = normalize_skill(requirement.skill_norm)
        if not skill_norm:
            continue

        if skill_norm not in selected_by_skill:
            selected_by_skill[skill_norm] = requirement
            order.append(skill_norm)
            continue

        if _requirement_rank(requirement) > _requirement_rank(selected_by_skill[skill_norm]):
            selected_by_skill[skill_norm] = requirement

    return [selected_by_skill[skill_norm] for skill_norm in order]


def _evidence_for_requirement(
    requirement: VacancyRequirement,
    *,
    profile: Profile,
    profile_blob: str,
    candidate_skills: set[str],
) -> dict[str, Any]:
    skill = normalize_skill(requirement.skill_norm)
    exact_skill_match = 1.0 if skill in candidate_skills else 0.0
    text_hits = _count_skill_hits(skill, profile_blob)
    related_skill_match, related_skill_hits = _related_skill_match(skill, candidate_skills)
    related_text_hits, related_text_sources = _related_text_hits(skill, profile_blob)
    total_text_hits = text_hits + related_text_hits
    text_density = min(total_text_hits / 3, 1.0)
    context = _context_score(skill, profile)
    evidence_score = min(
        1.0,
        (0.65 * exact_skill_match)
        + (0.35 * related_skill_match)
        + (0.25 * text_density)
        + (0.10 * context),
    )

    if evidence_score >= 0.65:
        status = "matched"
    elif evidence_score >= 0.35:
        status = "partial"
    else:
        status = "missing"

    sources: list[str] = []
    if exact_skill_match:
        sources.append("раздел навыков")
    if related_skill_hits:
        sources.append("родственные навыки: " + ", ".join(related_skill_hits))
    if context >= 1.0:
        sources.append("опыт или проекты")
    elif context > 0:
        sources.append("описание профиля или сертификаты")
    if total_text_hits:
        sources.append(f"упоминаний в профиле: {total_text_hits}")
    if related_text_sources and not related_skill_hits:
        sources.append("родственные упоминания: " + ", ".join(related_text_sources))

    return {
        "requirement_id": requirement.id,
        "skill_norm": skill,
        "display_name": requirement.display_name or skill,
        "category": requirement.category or "other",
        "source_text": requirement.source_text,
        "source_text_short": _compact_text(requirement.source_text),
        "confidence": requirement.confidence,
        "type": _requirement_type_value(requirement),
        "status": status,
        "evidence": round(evidence_score, 3),
        "evidence_percent": round(evidence_score * 100),
        "exact_skill_match": bool(exact_skill_match),
        "related_skill_match": bool(related_skill_match),
        "text_hits": total_text_hits,
        "context_score": context,
        "source": ", ".join(sources) if sources else "подтверждение не найдено",
    }


def _weighted_percent(items: list[dict[str, Any]], requirement_type: str) -> float:
    filtered = [item for item in items if item["type"] == requirement_type]
    if not filtered:
        return 0.0
    return round(sum(item["evidence"] for item in filtered) / len(filtered) * 100, 1)


def build_match_result(profile: Profile, vacancy: Vacancy) -> dict[str, Any]:
    profile_blob = _profile_text(profile)
    candidate_skills = {normalize_skill(skill.skill_norm) for skill in profile.skills}
    requirements = _deduplicate_vacancy_requirements(list(vacancy.requirements))

    items = [
        _evidence_for_requirement(
            requirement,
            profile=profile,
            profile_blob=profile_blob,
            candidate_skills=candidate_skills,
        )
        for requirement in requirements
    ]

    must_items = [item for item in items if item["type"] == RequirementType.MUST.value]
    nice_items = [item for item in items if item["type"] == RequirementType.NICE.value]
    must_pct = _weighted_percent(items, RequirementType.MUST.value)
    nice_pct = _weighted_percent(items, RequirementType.NICE.value)

    if must_items and nice_items:
        total_pct = round((must_pct * 0.75) + (nice_pct * 0.25), 1)
    elif must_items:
        total_pct = must_pct
    elif nice_items:
        total_pct = nice_pct
    else:
        total_pct = 0.0

    matched = [item for item in items if item["status"] == "matched"]
    partial = [item for item in items if item["status"] == "partial"]
    missing = [item for item in items if item["status"] == "missing"]

    return {
        "total_pct": total_pct,
        "must_pct": must_pct,
        "nice_pct": nice_pct,
        "matched_count": len(matched),
        "partial_count": len(partial),
        "missing_count": len(missing),
        "must_count": len(must_items),
        "nice_count": len(nice_items),
        "requirements": items,
        "matched": matched,
        "partial": partial,
        "missing": missing,
        "formula": {
            "evidence": "0.65 * exact_skill_match + 0.35 * related_skill_match + 0.25 * min(text_hits / 3, 1) + 0.10 * context_score",
            "total_pct": "0.75 * must_pct + 0.25 * nice_pct, если есть оба класса требований",
        },
    }


def build_low_match_warning(match_result: dict[str, Any] | None) -> str | None:
    if not match_result:
        return None

    try:
        total_pct = float(match_result.get("total_pct", 0.0))
    except (TypeError, ValueError):
        return None

    if total_pct >= LOW_MATCH_WARNING_THRESHOLD:
        return None

    return (
        f"Текущий match ниже {LOW_MATCH_WARNING_THRESHOLD:.0f}%. "
        "Профиль почти не соответствует выбранной вакансии: документы можно собрать как черновик, "
        "но перед откликом лучше закрыть ключевые пробелы или выбрать более близкую вакансию."
    )
