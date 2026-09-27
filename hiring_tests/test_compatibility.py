import pytest
from hiring.compatibility import compatibility
from hiring.matching import ALIASES, RELATED, evidence, extract


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
    assert len(RELATED) > 10


@pytest.mark.parametrize('source,target', [('pytest','unit testing'), ('selenium','automated testing'),
    ('bpmn','business process modeling'), ('user stories','requirements analysis'),
    ('roadmap','project management'), ('customer development','product management')])
def test_broader_related_evidence_is_partial_and_directional(source,target):
    result = compatibility('Использовал ' + source + ' в проекте.', {}, [requirement(target)])
    assert result['requirements'][0]['state'] == 'inferred'
    assert 0 < result['total_pct'] < 65
    reverse = evidence('Использовал ' + target, {}, [requirement(source)])
    assert reverse['requirements'][0]['state'] == 'unknown'


@pytest.mark.parametrize('text,answer,state', [('SQL не знаю.','', 'negative'),
    ('Хочу изучить SQL.','', 'review'), ('Использовал SQL.','Нет опыта SQL.', 'conflict'),
    ('Нет опыта SQL.','Создал проект на PostgreSQL.', 'conflict'),
    ('Есть опыт работы с каталогом.','Пропускаю уточнение, сведений недостаточно.', 'review')])
def test_negative_uncertain_and_conflicting_evidence_never_gets_points(text,answer,state):
    result = compatibility(text, {'r':answer} if answer else {}, [requirement('sql')])
    row = result['requirements'][0]
    assert row['state'] == state and row['evidence_percent'] == result['total_pct'] == 0


def test_formula_buckets_deduplication_and_empty_requirements():
    items = [requirement('python'), requirement('docker','nice','n')]
    result = compatibility('Создал сервис Python. Использовал Python для каталога. Написал Python тесты.', {}, items)
    assert result['must_pct'] == 100 and result['nice_pct'] == 0 and result['total_pct'] == 75
    assert compatibility('', {}, [])['total_pct'] is None
    duplicate = compatibility('Python', {}, [requirement('питон','nice','n'),requirement('python')])
    assert duplicate['must_count'] == 1 and duplicate['nice_count'] == 0
    assert duplicate['total_pct'] < 100
    spam = compatibility('Python ' * 100, {}, [requirement('python')])
    assert spam['total_pct'] == duplicate['total_pct']


def test_negated_optional_alias_not_promoted_to_must():
    rows = extract('Питон не требуется.\nБудет плюсом:\nпереговоров\npostres')
    assert 'python' not in {r['skill'] for r in rows}
    assert all(r['type'] == 'nice' for r in rows)


def test_generic_api_is_not_rest_and_fastapi_is_not_two_requirements():
    assert {r['skill'] for r in extract('Требования: Fast API')} == {'fastapi'}
    assert evidence('Разработал API.', {}, [requirement('rest api')])['covered'] == 0
