"""Local administrator bootstrap for a MAX-only employer; never invoke from HTTP."""
import argparse
import json
from dotenv import load_dotenv
from .config import Config
from .db import IntegrationKey, User, connect, serialize_writes
from .integrations import KeyCreate, issue_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id', required=True, help='Verified internal employer ID (not MAX chat ID)')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--name', help='Issue a new named key')
    mode.add_argument('--revoke', help='Revoke the specified key ID')
    parser.add_argument('--scope', action='append', dest='scopes')
    parser.add_argument('--days', type=int, default=30)
    args = parser.parse_args()
    load_dotenv('.env.hiring')
    config = Config()
    config.validate()
    body = KeyCreate(name=args.name, expires_in_days=args.days, **({'scopes': args.scopes} if args.scopes else {})) if args.name else None
    engine, factory = connect(config.database_url)
    try:
        with factory() as db:
            serialize_writes(db)
            owner = db.get(User, args.owner_id)
            if not owner or owner.role != 'employer' or (owner.max_id or '').startswith('sandbox:'):
                raise SystemExit('Verified ordinary employer not found; nothing issued')
            if args.revoke:
                row = db.get(IntegrationKey, args.revoke)
                if not row or row.owner_id != owner.id:
                    raise SystemExit('Owned key not found; nothing changed')
                row.revoked = True
                db.commit()
                print(json.dumps({'id': row.id, 'revoked': True}))
                return
            row, token = issue_key(db, owner, body)
            db.commit()
            # Explicit administrator command: one-time secret output, do not log/share.
            print(json.dumps({'id': row.id, 'token': token, 'scopes': row.scopes,
                              'expires_at': row.expires_at.isoformat()}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
