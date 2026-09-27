"""
Рекомендательный модуль для объяснимых советов по резюме и развитию навыков.
"""
from __future__ import annotations

from typing import Any

from apps.api.db.models import Profile, RequirementType, Vacancy
from apps.web.services.llm_service import LlmServiceError, improve_recommendation_actions_with_llm
from core.config import settings
from core.utils import display_skill_name, is_soft_skill, normalize_skill


def _match_bucket_weights(requirements: list[dict[str, Any]]) -> dict[str, float]:
    has_must = any(item["type"] == RequirementType.MUST.value for item in requirements)
    has_nice = any(item["type"] == RequirementType.NICE.value for item in requirements)
    if has_must and has_nice:
        return {
            RequirementType.MUST.value: 75.0,
            RequirementType.NICE.value: 25.0,
        }
    if has_must:
        return {RequirementType.MUST.value: 100.0, RequirementType.NICE.value: 0.0}
    if has_nice:
        return {RequirementType.MUST.value: 0.0, RequirementType.NICE.value: 100.0}
    return {RequirementType.MUST.value: 0.0, RequirementType.NICE.value: 0.0}


def _impact_points(
    item: dict[str, Any],
    *,
    type_counts: dict[str, int],
    bucket_weights: dict[str, float],
) -> float:
    requirement_type = item["type"]
    count = type_counts.get(requirement_type, 0)
    if count <= 0:
        return 0.0

    missing_part = max(0.0, 1.0 - float(item["evidence"]))
    return round(((bucket_weights.get(requirement_type, 0.0) / count) * missing_part) + 1e-9, 1)


def _effort(item: dict[str, Any]) -> str:
    if item["status"] == "partial" or item["text_hits"] > 0:
        return "низкая"
    if item["type"] == RequirementType.NICE.value:
        return "средняя"
    return "высокая"


MIN_VISIBLE_IMPACT = 0.1
MATCHED_BUT_WEAK_EVIDENCE = 0.9
MOTIVATION_SOFT_KEYWORDS = (
    "интерес",
    "мотивац",
    "желание",
    "готовность",
    "развиват",
    "обуч",
    "учиться",
)


def _requirement_name(item: dict[str, Any]) -> str:
    return display_skill_name(str(item.get("display_name") or item["skill_norm"]))


def _is_soft_requirement(item: dict[str, Any]) -> bool:
    return item.get("category") == "soft_skill" or is_soft_skill(str(item["skill_norm"]))


def _resume_context_recommendation(item: dict[str, Any], requirement_name: str, label: str, impact: float) -> dict[str, Any]:
    if _is_soft_requirement(item):
        return {
            "class": "resume_edit",
            "title": f"Показать {requirement_name} через пример",
            "reason": "Качество отмечено в профиле, но почти не подтверждено ситуацией из опыта, проекта или учебной практики.",
            "action": "Добавьте короткий честный пример: где это качество проявилось, какую задачу помогло решить и какой был результат.",
            "impact_on_match": impact,
            "impact_kind": "exact",
            "effort": "низкая",
            "based_on": f"{label} требование вакансии; качество есть в профиле, но не раскрыто примером",
        }

    return {
        "class": "resume_edit",
        "title": f"Подкрепить {requirement_name} контекстом",
        "reason": "Навык найден, но пока слабо раскрыт в опыте или проектах. Семантические ATS могут дать меньше баллов, если навык есть только в списке.",
        "action": "Если это соответствует вашему реальному опыту, добавьте честное упоминание навыка в релевантную задачу, проект или достижение.",
        "impact_on_match": impact,
        "impact_kind": "exact",
        "effort": "низкая",
        "based_on": f"{label} требование вакансии; навык есть в профиле, но не раскрыт примером",
    }


def _partial_recommendation(item: dict[str, Any], requirement_name: str, label: str, impact: float) -> dict[str, Any]:
    if _is_soft_requirement(item):
        return {
            "class": "resume_edit",
            "title": f"Уточнить, где проявляется {requirement_name}",
            "reason": "Система видит частичное совпадение, но не хватает понятного подтверждения в контексте опыта.",
            "action": "Опишите один конкретный эпизод: задача, ваше действие, результат. Не добавляйте качество, если не можете подтвердить его примером.",
            "impact_on_match": impact,
            "impact_kind": "exact",
            "effort": _effort(item),
            "based_on": f"{label} требование вакансии; источник: {item['source']}",
        }

    return {
        "class": "resume_edit",
        "title": f"Уточнить {requirement_name} в резюме",
        "reason": "Система нашла частичное подтверждение, но совпадение недостаточно сильное.",
        "action": "Если навык действительно есть, внесите его в раздел навыков и покажите в описании опыта или учебного проекта.",
        "impact_on_match": impact,
        "impact_kind": "exact",
        "effort": _effort(item),
        "based_on": f"{label} требование вакансии; источник: {item['source']}",
    }


def _missing_recommendation(item: dict[str, Any], requirement_name: str, label: str, impact: float) -> dict[str, Any]:
    if _is_soft_requirement(item):
        title = f"Отработать и подтвердить {requirement_name}"
        reason = "Вакансия явно просит это качество, но в профиле нет достаточного подтверждения."
        if any(marker in requirement_name.lower() for marker in MOTIVATION_SOFT_KEYWORDS):
            action = "Покажите это не как абстрактное качество, а через траекторию: какие темы изучаете, какие проекты выбрали и как закрепляете практику. Если подтверждений пока нет, лучше сначала добавить реальный учебный или pet-проект в профиль."
        else:
            action = "Найдите учебную, проектную или рабочую ситуацию, где это качество можно честно подтвердить примером. После этого добавьте в профиль короткий контекст: задача, ваше действие и результат."
    elif item.get("category") in {"engineering_practice", "database_practice", "architecture", "api"}:
        title = f"Отработать практику: {requirement_name}"
        reason = "Это не просто слово для списка навыков, а практический опыт, который лучше подтверждать задачей или проектом."
        action = "Сделайте небольшой практический кейс: примените навык, зафиксируйте результат и добавьте его в профиль как проект или задачу."
    elif item["type"] == RequirementType.NICE.value:
        title = f"Усилить отклик через {requirement_name}"
        reason = "Это желательное требование. Оно не блокирует отклик, но может повысить релевантность кандидата."
        action = "Если навык интересен и близок к вашему стеку, изучите базовый сценарий и закрепите его в pet-проекте. Если нет, не добавляйте его искусственно."
    else:
        title = f"Закрыть пробел: {requirement_name}"
        reason = "Это обязательное требование вакансии, а в профиле пока нет достаточного подтверждения."
        action = "Изучите базовые сценарии, примените навык в небольшом проекте или учебной задаче и после этого добавьте подтверждение в профиль."

    return {
        "class": "learn_and_practice",
        "title": title,
        "reason": reason,
        "action": action,
        "impact_on_match": impact,
        "impact_kind": "exact",
        "effort": _effort(item),
        "based_on": f"{label} требование вакансии; подтверждение в профиле не найдено",
    }


def _maybe_improve_actions_with_ai(
    vacancy: Vacancy,
    recommendations: dict[str, list[dict[str, Any]]],
    *,
    use_ai_actions: bool,
) -> None:
    if not use_ai_actions or not settings.deepseek_api_key:
        return

    indexed: list[tuple[int, dict[str, Any]]] = []
    for rec in recommendations["resume_edit"] + recommendations["learn_and_practice"]:
        indexed.append((len(indexed), rec))
        if len(indexed) >= 12:
            break

    if not indexed:
        return

    payload = {
        "vacancy_title": vacancy.title,
        "recommendations": [
            {
                "index": index,
                "class": rec.get("class"),
                "title": rec.get("title"),
                "reason": rec.get("reason"),
                "current_action": rec.get("action"),
                "based_on": rec.get("based_on"),
                "effort": rec.get("effort"),
            }
            for index, rec in indexed
        ],
    }

    try:
        improved = improve_recommendation_actions_with_llm(payload)
    except (LlmServiceError, ValueError, TypeError):
        return

    rec_by_index = {index: rec for index, rec in indexed}
    for item in improved:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        action = str(item.get("action") or "").strip()
        if index in rec_by_index and action:
            rec_by_index[index]["action"] = action[:700]
            rec_by_index[index]["action_source"] = "ai"


def build_recommendations(
    profile: Profile,
    vacancy: Vacancy,
    match_result: dict[str, Any],
    *,
    use_ai_actions: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    requirements = match_result["requirements"]
    type_counts = {
        RequirementType.MUST.value: sum(1 for item in requirements if item["type"] == RequirementType.MUST.value),
        RequirementType.NICE.value: sum(1 for item in requirements if item["type"] == RequirementType.NICE.value),
    }
    bucket_weights = _match_bucket_weights(requirements)
    resume_edit: list[dict[str, Any]] = []
    learn_and_practice: list[dict[str, Any]] = []
    for item in requirements:
        impact = _impact_points(item, type_counts=type_counts, bucket_weights=bucket_weights)
        label = "обязательное" if item["type"] == RequirementType.MUST.value else "желательное"
        requirement_name = _requirement_name(item)

        if item["status"] == "matched":
            if (
                item["exact_skill_match"]
                and item["context_score"] < 1
                and float(item["evidence"]) < MATCHED_BUT_WEAK_EVIDENCE
                and impact > MIN_VISIBLE_IMPACT
            ):
                resume_edit.append(_resume_context_recommendation(item, requirement_name, label, impact))
            continue

        if item["status"] == "partial":
            resume_edit.append(_partial_recommendation(item, requirement_name, label, impact))

        if item["status"] == "missing":
            learn_and_practice.append(_missing_recommendation(item, requirement_name, label, impact))

    if not profile.summary_text:
        resume_edit.append(
            {
                "class": "resume_edit",
                "title": "Добавить раздел «О себе»",
                "reason": "ATS и рекрутеру проще понять релевантность, когда есть короткая профессиональная связка опыта с вакансией.",
                "action": "Напишите 2-4 предложения: роль, основной стек, тип задач и направление, на которое претендуете.",
                "impact_on_match": 3.0,
                "impact_kind": "heuristic",
                "effort": "низкая",
                "based_on": "правило ATS-дружественной структуры резюме",
            }
        )

    if not getattr(profile, "projects_text", None) and not getattr(profile, "experience_text", None):
        resume_edit.append(
            {
                "class": "resume_edit",
                "title": "Добавить релевантные проекты или учебный опыт",
                "reason": "Для начинающего специалиста учебные проекты могут быть доказательством навыков, если они связаны с вакансией.",
                "action": "Опишите 1-3 наиболее релевантных проекта: задача, стек, ваша роль, результат без выдуманных метрик.",
                "impact_on_match": 5.0,
                "impact_kind": "heuristic",
                "effort": "средняя",
                "based_on": "нет заполненного опыта или проектов в профиле",
            }
        )

    resume_edit.sort(key=lambda rec: rec["impact_on_match"], reverse=True)
    learn_and_practice.sort(key=lambda rec: rec["impact_on_match"], reverse=True)
    recommendations = {
        "resume_edit": resume_edit,
        "learn_and_practice": learn_and_practice,
    }
    _maybe_improve_actions_with_ai(vacancy, recommendations, use_ai_actions=use_ai_actions)
    return recommendations
