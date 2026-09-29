"""Development-only MAX long polling. Production must use HTTPS webhook."""
import argparse
import json
import logging
import os
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import String, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from .config import Config
from .db import Base, connect
from .bot import process_event, start_worker
from .max_client import tls_context
from .sandbox import check_storage


@contextmanager
def polling_lock(database_url):
    """Allow one long-polling process per persistent SQLite database."""
    source = make_url(database_url)
    if source.get_backend_name() != 'sqlite' or not source.database or source.database == ':memory:':
        raise ValueError('Polling requires a persistent SQLite database')
    path = Path(source.database).resolve().with_suffix('.polling.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    locked = False
    try:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError('Polling is already running for this database') from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()  # The OS releases the lock on crashes; keep the harmless file.


class PollCursor(Base):
    __tablename__ = "hiring_poll_cursor"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    marker: Mapped[str] = mapped_column(String(64))


def transport_url(database_url):
    """Both modes use one local transport cursor/lock, independent of domain DB."""
    source = make_url(database_url)
    if source.get_backend_name() != 'sqlite' or not source.database or source.database == ':memory:':
        raise ValueError('Polling requires a persistent SQLite database')
    path = Path(source.database).resolve().with_name('hiring.transport.db')
    return str(source.set(database=str(path)))


def open_cursor_store(url, legacy_factory, bot_id):
    engine = create_engine(url)
    PollCursor.__table__.create(engine, checkfirst=True)
    factory = sessionmaker(engine)
    with factory() as db:
        if not db.get(PollCursor, bot_id):
            # One-time migration from the currently running mode. Never replace
            # a shared marker with a stale legacy marker on subsequent switches.
            with legacy_factory() as legacy:
                row = legacy.get(PollCursor, bot_id)
                if row:
                    db.add(PollCursor(id=bot_id, marker=row.marker))
                    db.commit()
    return engine, factory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Check token and subscriptions, do not consume events")
    parser.add_argument('--sandbox', action='store_true', help='Use isolated data/hiring.sandbox.db with virtual test personas')
    parser.add_argument('--test-user', action='append', default=[], help='Allowlisted physical MAX user ID; repeat for another tester')
    args = parser.parse_args()
    load_dotenv(".env.hiring")
    config = Config()
    cursor_url = transport_url(config.database_url) if not args.check else None
    if args.test_user and not args.sandbox:
        parser.error('--test-user requires --sandbox')
    if args.sandbox:
        config.sandbox, config.sandbox_users = True, tuple(args.test_user)
        config.database_url = 'sqlite:///data/hiring.sandbox.db'
    config.validate()
    if not config.bot_token:
        raise SystemExit("Set MAX_BOT_TOKEN in .env.hiring")
    if config.production and not args.check:
        raise SystemExit("Production requires webhook, not polling")
    # HTTP debug logging may contain request headers. Never enable it here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with (nullcontext() if args.check else polling_lock(cursor_url)), httpx.Client(base_url=config.max_api_url, headers={"Authorization": config.bot_token}, verify=tls_context(config.max_api_url), timeout=40) as api:
        info = api.get("/me")
        if info.status_code != 200:
            raise SystemExit(f"MAX /me HTTP {info.status_code}: check token (never printed)")
        bot = info.json()
        config.bot_name = bot.get("username") or str(bot["user_id"])
        subscriptions = api.get("/subscriptions")
        subscriptions.raise_for_status()
        count = len(subscriptions.json().get("subscriptions", []))
        print(json.dumps({"bot": config.bot_name, "url": f"https://max.ru/{config.bot_name}", "webhooks": count}, ensure_ascii=False), flush=True)
        if args.check:
            return
        if count:
            raise SystemExit("Bot already has a webhook. Polling was not started; existing subscriptions were not changed.")
        if not config.employer_code:
            raise SystemExit("Set HIRING_EMPLOYER_CODE before starting polling")
        Path("data").mkdir(exist_ok=True)
        engine, factory = connect(config.database_url)
        if config.sandbox:
            check_storage(factory)
        cursor_engine, cursor_factory = open_cursor_store(cursor_url, factory, str(bot['user_id']))
        worker = start_worker(factory, config)
        print("MAX polling started; waiting for /start. Keep this process running.", flush=True)
        if config.sandbox:
            print('SANDBOX ONLY: allowlisted testers must send /test. Other users are ignored; normal database is not used.', flush=True)
        try:
            while True:
                with cursor_factory() as db:
                    cursor = db.get(PollCursor, str(bot["user_id"]))
                    marker = cursor.marker if cursor else None
                params = {"timeout": 25, "limit": 50, "types": "bot_started,message_created,message_callback"}
                if marker is not None:
                    params["marker"] = marker
                try:
                    result = api.get("/updates", params=params)
                    if result.status_code in (401, 403, 405):
                        raise SystemExit(f"Polling stopped: MAX HTTP {result.status_code}")
                    result.raise_for_status()
                    payload = result.json()
                    for event in payload.get("updates", []):
                        process_event(factory, event, config)
                    if payload.get("marker") is not None:
                        with cursor_factory() as db:
                            db.merge(PollCursor(id=str(bot["user_id"]), marker=str(payload["marker"])))
                            db.commit()
                    if payload.get("updates"):
                        print(f"Processed {len(payload['updates'])} update(s)", flush=True)
                except (httpx.HTTPError, ValueError, SQLAlchemyError):
                    print("MAX temporarily unavailable; retrying in 5 seconds", flush=True)
                    time.sleep(5)
        except KeyboardInterrupt:
            print("Polling stopped")
        finally:
            worker[0].set()
            worker[1].join(timeout=15)
            engine.dispose()
            cursor_engine.dispose()


if __name__ == "__main__":
    main()
