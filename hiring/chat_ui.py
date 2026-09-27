"""Persisted, user-bound buttons: payloads contain no business data or credentials."""
from datetime import timedelta, timezone
from .db import BotAction, Outbox, now

PAGE_SIZE = 5


def queue_message(db, user, text, buttons=None, application_id=None, state=None):
    body = {'text': text[:3900]}
    if buttons:
        rows = []
        for label, command in buttons:
            action = BotAction(user_id=user.id, command=command,
                               expected_state=state, expires_at=now() + timedelta(hours=24))
            db.add(action)
            db.flush()
            rows.append([{'type': 'callback', 'text': label[:80], 'payload': action.id}])
        body['attachments'] = [{'type': 'inline_keyboard', 'payload': {'buttons': rows}}]
    db.add(Outbox(max_id=user.max_id, body=body, application_id=application_id))


def consume_action(db, user, session, payload):
    if not isinstance(payload, str) or len(payload) != 32:
        return None
    action = db.get(BotAction, payload)
    if not action or action.user_id != user.id or action.used:
        return None
    if action.expires_at.replace(tzinfo=timezone.utc) <= now():
        return None
    if action.expected_state is not None and action.expected_state != session.state:
        return None
    action.used = True
    return action.command


def page_number(value):
    try:
        return max(0, min(10000, int(value)))
    except (TypeError, ValueError):
        return 0
