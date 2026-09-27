from __future__ import annotations

from types import SimpleNamespace

from apps.api.db.models import RequirementType
from apps.web.services.match_service import build_low_match_warning, build_match_result


def test_build_match_result_ignores_duplicate_requirements_by_skill():
    profile = SimpleNamespace(
        target_position="Backend-разработчик",
        summary_text="",
        experience_text="",
        projects_text="",
        education_text="",
        certificates_text="",
        languages_text="",
        soft_skills_text="",
        skills=[],
    )
    vacancy = SimpleNamespace(
        requirements=[
            SimpleNamespace(
                id=1,
                type=RequirementType.MUST,
                skill_norm="клиентоориентированность",
                display_name="клиентоориентированность",
                category="other",
                source_text="Ключевые навыки: Клиентоориентированность",
                confidence=0.4,
            ),
            SimpleNamespace(
                id=2,
                type=RequirementType.MUST,
                skill_norm="client orientation",
                display_name="Клиентоориентированность",
                category="soft_skill",
                source_text="Требования: клиентоориентированность",
                confidence=0.9,
            ),
            SimpleNamespace(
                id=3,
                type=RequirementType.MUST,
                skill_norm="cold sales",
                display_name="Холодные продажи",
                category="sales_skill",
                source_text="Требования: холодные продажи",
                confidence=0.9,
            ),
        ],
    )

    match_result = build_match_result(profile, vacancy)

    assert match_result["must_count"] == 2
    assert [item["skill_norm"] for item in match_result["requirements"]] == [
        "client orientation",
        "cold sales",
    ]
    assert match_result["requirements"][0]["category"] == "soft_skill"


def test_low_match_warning_is_shown_only_for_weak_matches():
    assert build_low_match_warning({"total_pct": 6.1})
    assert build_low_match_warning({"total_pct": 35.0}) is None
