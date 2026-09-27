"""Regression examples use synthetic data only; no live MAX calls."""
import pytest
from sqlalchemy import select
from hiring.bot import process_event
from hiring.db import User
from hiring.matching import evidence
from test_product import client


@pytest.mark.parametrize('resume, expected', [
    ('Python не изучал.', 'negative'), ('Python не владею.', 'negative'),
    ('Не использовала Python.', 'negative'), ('Нет опыта с Python.', 'negative'),
    ('Без опыта Python.', 'negative'), ('Python не знаю.', 'negative'),
    ('С Python не знаком.', 'negative'), ('Не доводилось работать с Python.', 'negative'),
    ('Python использовал, Docker не использовал.', 'mentioned'),
    ('Python использовал и Docker не использовал.', 'mentioned'),
    ('Не знаю Docker, Python использовал в проекте.', 'mentioned'),
    ('Python использовал. Python не знаю.', 'conflict'),
    ('Знаю Python.', 'mentioned'), ('Работаю на питоне.', 'mentioned'),
    ('Python: написал сервис.', 'mentioned'), ('Работал с python!', 'mentioned'),
    ('Пишу на Java.', 'unknown'), ('Pythonista — название клуба.', 'unknown'),
    ('Только Docker.', 'unknown'), ('Опыт не указан.', 'unknown'),
    ('Хочу изучить Python.', 'review'), ('Планирую освоить Python.', 'review'),
    ('Возможно, знаю Python.', 'review'), ('Python — слышал о нём.', 'review'),
    ('Не только Python, но и SQL.', 'review'),
    ('I have never used Python.', 'negative'), ('I do not know Python.', 'negative'),
    ('Python: no experience.', 'negative'), ('I used Python.', 'mentioned'),
    ('I want to learn Python.', 'review'),
])
def test_matching_corpus(resume, expected):
    req = [{'id': 'p', 'skill': 'python', 'label': 'Python', 'type': 'must'}]
    assert evidence(resume, {}, req)['requirements'][0]['state'] == expected


@pytest.mark.parametrize('event', [None, [], 'bad', {},
    {'update_type': 'message_created', 'message': {'recipient': 'invalid', 'body': {}}},
    {'update_type': 'message_created', 'message': {'body': []}},
    {'update_type': 'message_callback', 'callback': []},
    {'update_type': 'message_created', 'message': {'sender': {'user_id': True}, 'body': {}}},
])
def test_malformed_updates_do_not_crash_or_create_users(client, event):
    process_event(client.app.state.factory, event, client.app.state.config)
    with client.app.state.factory() as db:
        assert db.scalar(select(User)) is None
