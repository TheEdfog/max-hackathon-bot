from types import SimpleNamespace

from apps.web.services.recommendation_service import build_recommendations


def _item(skill: str, status: str, evidence: float, **overrides):
    item = {
        "skill_norm": skill,
        "type": "must",
        "status": status,
        "evidence": evidence,
        "exact_skill_match": True,
        "text_hits": 3,
        "context_score": 1.0,
        "source": "раздел навыков, опыт или проекты",
    }
    item.update(overrides)
    return item


def test_matched_requirements_do_not_become_learning_recommendations():
    profile = SimpleNamespace(summary_text="Есть описание", experience_text="Есть опыт", projects_text="Есть проект")
    match_result = {
        "requirements": [
            _item("c#", "matched", 1.0),
            _item("react", "matched", 1.0, type="nice"),
            _item("dotnet", "matched", 0.733, context_score=0.0, text_hits=1),
            _item("kubernetes", "missing", 0.0, exact_skill_match=False, text_hits=0, context_score=0.0),
        ]
    }

    recommendations = build_recommendations(profile, SimpleNamespace(), match_result)

    edit_titles = [item["title"] for item in recommendations["resume_edit"]]
    learn_titles = [item["title"] for item in recommendations["learn_and_practice"]]

    assert edit_titles == ["Подкрепить .NET контекстом"]
    assert learn_titles == ["Закрыть пробел: Kubernetes"]


def test_profile_soft_skill_without_vacancy_requirement_is_not_recommended():
    profile = SimpleNamespace(
        summary_text="Backend-разработчик с опытом API.",
        experience_text="Разрабатывал сервисы на Python.",
        projects_text="Проектировал REST API.",
        skills=[SimpleNamespace(skill_norm="communication")],
    )
    match_result = {"requirements": []}

    recommendations = build_recommendations(profile, SimpleNamespace(), match_result)

    assert recommendations["resume_edit"] == []


def test_requirement_impact_is_potential_total_match_gain_in_percentage_points():
    profile = SimpleNamespace(
        summary_text="Есть описание",
        experience_text="Есть опыт",
        projects_text="Есть проект",
        skills=[],
    )
    match_result = {
        "requirements": [
            _item("python", "matched", 1.0),
            _item("fastapi", "matched", 0.918),
            _item("postgresql", "partial", 0.518, exact_skill_match=False, text_hits=1, context_score=0.0),
            _item("docker", "matched", 0.918, type="nice"),
            _item("redis", "missing", 0.0, type="nice", exact_skill_match=False, text_hits=0, context_score=0.0),
        ]
    }

    recommendations = build_recommendations(profile, SimpleNamespace(), match_result)

    edit_by_title = {item["title"]: item for item in recommendations["resume_edit"]}
    learn_by_title = {item["title"]: item for item in recommendations["learn_and_practice"]}

    assert edit_by_title["Уточнить PostgreSQL в резюме"]["impact_on_match"] == 12.1
    assert edit_by_title["Уточнить PostgreSQL в резюме"]["impact_kind"] == "exact"
    assert learn_by_title["Усилить отклик через Redis"]["impact_on_match"] == 12.5
    assert learn_by_title["Усилить отклик через Redis"]["impact_kind"] == "exact"
