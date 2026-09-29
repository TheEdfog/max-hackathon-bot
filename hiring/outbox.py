"""Durable bounded retries; serialized send-vs-withdraw ordering on SQLite."""
import logging
import threading
from datetime import timedelta
import httpx
from sqlalchemy import case, select
from .db import Application, ImportTask, Outbox, now, serialize_writes
from .max_client import tls_context

log = logging.getLogger('hiring.outbox')


def deliver_one(factory, config, post=None):
    if not config.bot_token:
        return False
    post = post or httpx.post
    with factory() as db:
        serialize_writes(db)
        row = db.scalar(select(Outbox).where(Outbox.status == 'pending', Outbox.available_at <= now()).order_by(
            case((Outbox.callback_id.is_not(None), 0), else_=1), Outbox.available_at, Outbox.id).limit(1))
        if not row:
            return False
        app = db.get(Application, row.application_id) if row.application_id else None
        task = db.get(ImportTask, row.import_id) if row.import_id else None
        if ((app and app.status == 'withdrawn') or (row.import_id and
                (not task or task.status != 'ready' or task.expires_at.replace(tzinfo=now().tzinfo) <= now()))):
            row.body, row.status = {}, 'cancelled'
            db.commit()
            return True
        row.attempts += 1
        endpoint = '/answers' if row.callback_id else '/messages'
        params = {'callback_id': row.callback_id} if row.callback_id else {'user_id': row.max_id}
        status, retry_after, success = 0, 0, False
        try:
            response = post(config.max_api_url + endpoint, params=params,
                            headers={'Authorization': config.bot_token}, json=row.body,
                            timeout=12, verify=tls_context(config.max_api_url))
            status = response.status_code
            result = response.json() if response.is_success else {}
            success = response.is_success and isinstance(result, dict) and (
                result.get('success') is True if row.callback_id else isinstance(result.get('message'), dict))
            if status == 429:
                try:
                    retry_after = min(3600, max(1, int(response.headers.get('Retry-After', '60'))))
                except ValueError:
                    retry_after = 60
        except (httpx.HTTPError, ValueError):
            pass
        permanent = status in (400, 401, 403, 404, 405, 410, 422)
        row.status = 'sent' if success else ('failed' if permanent or row.attempts >= 6 else 'pending')
        row.available_at = now() + timedelta(seconds=max(retry_after, min(300, 2 ** row.attempts)))
        if success:
            row.body = {}
        else:
            log.warning('outbox delivery status=%s http=%s attempts=%s', row.status, status, row.attempts)
        db.commit()
        return True


def start_worker(factory, config):
    stop = threading.Event()

    def run():
        while not stop.wait(0.7):
            try:
                deliver_one(factory, config)
            except Exception as exc:
                log.error('outbox worker error type=%s', type(exc).__name__)
                stop.wait(2)
    thread = threading.Thread(target=run, daemon=True, name='max-outbox')
    def import_loop():
        from .imports import deliver_import
        while not stop.wait(1):
            try:
                deliver_import(factory)
            except Exception as exc:
                log.error('import worker error type=%s', type(exc).__name__)
                stop.wait(2)

    imports = threading.Thread(target=import_loop, daemon=True, name='public-import')
    def ai_loop():
        from .ai_review import deliver_one as deliver_ai_review
        while not stop.wait(1.5):
            try:
                deliver_ai_review(factory, config)
            except Exception as exc:
                log.error('AI review worker error type=%s', type(exc).__name__)
                stop.wait(2)

    ai = threading.Thread(target=ai_loop, daemon=True, name='resume-ai-review')
    def ai_cleanup_loop():
        from .ai_review import purge_expired
        while not stop.wait(60):
            try:
                purge_expired(factory)
            except Exception as exc:
                log.error('AI retention cleanup error type=%s', type(exc).__name__)

    ai_cleanup = threading.Thread(target=ai_cleanup_loop, daemon=True, name='resume-ai-retention')
    class Workers:
        def join(self, timeout=None):
            thread.join(timeout=timeout)
            imports.join(timeout=timeout)
            ai.join(timeout=timeout)
            ai_cleanup.join(timeout=timeout)
    imports.start()
    thread.start()
    ai.start()
    ai_cleanup.start()
    return stop, Workers()
