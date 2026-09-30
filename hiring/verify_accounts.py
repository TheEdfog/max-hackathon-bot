"""Provision isolated DATA-API identities. Credentials are saved locally, never printed."""
import argparse
import json
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import select
from .config import Config
from .db import User, connect
from .security import hash_password

PUBLIC_REVIEW_PASSWORDS = {
    'employer': 'TheSwarmReviewEmployer2026!',
    'candidate': 'TheSwarmReviewCandidate2026!',
    'other_employer': 'TheSwarmReviewOther2026!',
}


def provision(database_url, destination, public_review=False):
    target = Path(destination)
    if target.exists():
        raise ValueError('Credential file exists; refusing to overwrite')
    target.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = connect(database_url)
    identities = {}
    try:
        with factory() as db:
            for role in ('employer', 'candidate', 'other_employer'):
                email = f'data-api-{role}@example.com'
                if db.scalar(select(User).where(User.email == email)):
                    raise ValueError('Verification account exists; preserve its credentials or use a fresh test database')
                password = (PUBLIC_REVIEW_PASSWORDS[role] if public_review
                            else secrets.token_urlsafe(24))
                db.add(User(email=email, password=hash_password(password), name='Synthetic ' + role,
                            company='Synthetic API verification', role='candidate' if role == 'candidate' else 'employer'))
                identities[role] = {'email': email, 'password': password}
            # Exclusive file creation prevents accidental overwrite. No MAX IDs are assigned.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(identities, handle, indent=2)
            db.commit()
    finally:
        engine.dispose()
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='data/verification-accounts.json')
    parser.add_argument('--isolated-test-db', action='store_true', required=True,
                        help='Confirm that this database is an isolated verification database')
    parser.add_argument('--public-review-accounts', action='store_true',
                        help='Use the disposable public test passwords documented in README; isolated DB only')
    args = parser.parse_args()
    load_dotenv('.env.hiring')
    config = Config()
    if args.public_review_accounts and not config.review_mode:
        parser.error('--public-review-accounts is allowed only when HIRING_REVIEW_MODE=true')
    target = provision(config.database_url, args.output, public_review=args.public_review_accounts)
    print('Synthetic accounts created. Keep credentials private:', target)


if __name__ == '__main__':
    main()
