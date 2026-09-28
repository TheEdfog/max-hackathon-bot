"""Explicit developer-only virtual actors. Real storage/authorization remain separate."""
import re
from sqlalchemy import select
from .db import BotSession, User

PERSONAS = {
    'e': ('Работодатель', 'employer', 'Тестовая компания А'),
    'c': ('Кандидат', 'candidate', ''),
    'x': ('Другой работодатель', 'employer', 'Тестовая компания Б'),
}
ALIAS = re.compile(r'^sandbox:([1-9][0-9]{0,18}):([ecx])$')


def identity(user):
    match = ALIAS.fullmatch(user.max_id or '')
    return (match[1], match[2]) if match else (user.max_id, None)


def destination(user):
    return identity(user)[0]


def actor(db, physical_id, persona):
    label, role, company = PERSONAS[persona]
    alias = f'sandbox:{physical_id}:{persona}'
    user = db.scalar(select(User).where(User.max_id == alias))
    if not user:
        user = User(max_id=alias, role=role, name='Тест: ' + label, company=company)
        db.add(user)
        db.flush()
    session = db.get(BotSession, user.id)
    if not session:
        session = BotSession(user_id=user.id, state={})
        db.add(session)
        db.flush()
    return user, session


def check_storage(factory):
    with factory() as db:
        if not (db.bind.url.database or '').endswith('.sandbox.db'):
            raise ValueError('Sandbox storage path rejected')
        if any(not ALIAS.fullmatch(value or '') for value in db.scalars(select(User.max_id))):
            raise ValueError('Refusing to use a database containing non-sandbox accounts')
