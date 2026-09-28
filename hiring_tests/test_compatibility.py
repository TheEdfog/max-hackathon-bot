import pytest
from hiring.matching import ALIASES, evidence, extract


def requirement(skill, kind='must', identifier='r'):
    return {'id': identifier, 'skill': skill, 'label': skill, 'type': kind}


@pytest.mark.parametrize('phrase,skill', [
    ('управлял проектами', 'project management'), ('переговоров', 'negotiation'),
    ('пользовательских историй', 'user stories'), ('сбором требований', 'requirements gathering'),
    ('дорожной карты', 'roadmap'), ('продуктовой аналитикой', 'product analytics'),
    ('кастдевов', 'customer development'), ('работа с возражениями', 'objection handling'),
    ('холодные продажи', 'cold sales'), ('клиентоориентированность', 'client orientation'),
    ('team player', 'teamwork'), ('unit тесты', 'unit testing'), ('fast api', 'fastapi'),
])
def test_broad_dictionary_used_for_resume_evidence(phrase, skill):
    text = 'Мой опыт: ' + phrase + '.'
    assert evidence(text, {}, [requirement(skill)])['requirements'][0]['state'] == 'mentioned'
    assert skill in {r['skill'] for r in extract('Требования:\n' + phrase)}


def test_entire_parser_vocabulary_available_not_only_python():
    from apps.web.services.vacancy_parser_service import KNOWN_SKILL_ALIASES
    from core.utils import normalize_skill
    for skill, aliases in KNOWN_SKILL_ALIASES.items():
        assert set(aliases) <= set(ALIASES[normalize_skill(skill)])
    assert len(ALIASES) > 150


def test_negated_optional_alias_not_promoted_to_must():
    rows = extract('Питон не требуется.\nБудет плюсом:\nпереговоров\npostres')
    assert 'python' not in {r['skill'] for r in rows}
    assert all(r['type'] == 'nice' for r in rows)


def test_generic_api_is_not_rest_and_fastapi_is_not_two_requirements():
    assert {r['skill'] for r in extract('Требования: Fast API')} == {'fastapi'}
    assert evidence('Разработал API.', {}, [requirement('rest api')])['mentioned'] == 0
