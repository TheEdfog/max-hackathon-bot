"""Local SQLite backup/inspection; never prints credentials or record contents."""
import argparse
import sqlite3
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from .config import Config
from .db import BotAction, BotEvent, Outbox, connect, now, serialize_writes
from sqlalchemy import delete, func, select


def backup(database_url, destination):
    source = Path(make_url(database_url).database or '').resolve()
    target = Path(destination).resolve()
    if not database_url.startswith('sqlite:') or not source.is_file() or source == target:
        raise ValueError('An existing SQLite source and a different new destination are required')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb'):
        pass  # Never overwrite an existing backup.
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'status', 'prune'])
    parser.add_argument('--output')
    parser.add_argument('--apply', action='store_true', help='Apply retention cleanup; default is preview')
    args = parser.parse_args()
    load_dotenv('.env.hiring')
    url = Config().database_url
    if args.command == 'backup':
        if not args.output:
            parser.error('backup requires --output')
        print('Backup verified:', backup(url, args.output))
        return
    engine, factory = connect(url)
    try:
        with factory() as db:
            if args.command == 'status':
                print('Outbox counts:', dict(db.execute(select(Outbox.status, func.count()).group_by(Outbox.status)).all()))
                print('Oldest pending:', db.scalar(select(func.min(Outbox.available_at)).where(Outbox.status == 'pending')))
            else:
                serialize_writes(db)
                rules = [(BotAction, BotAction.expires_at < now()),
                         (BotEvent, BotEvent.created_at < now() - timedelta(days=7)),
                         (Outbox, (Outbox.available_at < now() - timedelta(days=7)) & Outbox.status.in_(['sent', 'failed', 'cancelled']))]
                for model, where in rules:
                    count = db.scalar(select(func.count()).select_from(model).where(where))
                    print(model.__tablename__, count, 'delete' if args.apply else 'preview')
                    if args.apply:
                        db.execute(delete(model).where(where))
                if args.apply:
                    db.commit()
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
