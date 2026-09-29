"""Read-only configuration, SQLite and MAX diagnostics; never prints secrets/records."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import httpx
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from .config import Config
from .max_client import tls_context


def inspect_local(config):
    checks = []
    def add(name, ok, note):
        checks.append({'check': name, 'ok': bool(ok), 'note': note})
    try:
        config.validate()
        add('configuration', True, 'Configuration accepted; values are not displayed')
    except ValueError:
        add('configuration', False, 'Invalid configuration; compare with .env.hiring.example')
    add('python', sys.version_info[:2] == (3, 13), 'Release tested on Python 3.13')
    add('max_token', bool(config.bot_token), 'Set MAX_BOT_TOKEN in the private environment file')
    add('employer_code', bool(config.employer_code), 'Required for employer onboarding')
    url = make_url(config.database_url)
    path = Path(url.database or '')
    if url.get_backend_name() != 'sqlite' or not path.is_file():
        add('database', False, 'Persistent SQLite database not initialized; start the app first')
    else:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as db:
            add('database', db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'SQLite quick_check')
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'hiring_outbox' in names:
                counts = dict(db.execute('SELECT status, COUNT(*) FROM hiring_outbox GROUP BY status').fetchall())
                add('outbox', not counts.get('failed'), 'Counts only: ' + json.dumps(counts, sort_keys=True))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max', action='store_true', help='Also check MAX identity/subscriptions, without consuming updates or sending messages')
    args = parser.parse_args()
    load_dotenv('.env.hiring')
    config = Config()
    try:
        checks = inspect_local(config)
        if args.max and all(c['ok'] for c in checks if c['check'] in ('configuration', 'max_token')):
            with httpx.Client(base_url=config.max_api_url, headers={'Authorization': config.bot_token},
                              verify=tls_context(config.max_api_url), timeout=15, follow_redirects=False) as api:
                identity, subscriptions = api.get('/me'), api.get('/subscriptions')
                ok = identity.status_code == subscriptions.status_code == 200
                note = f'MAX identity HTTP {identity.status_code}; subscriptions HTTP {subscriptions.status_code}'
                if ok:
                    count = len(subscriptions.json().get('subscriptions', []))
                    note += f'; webhooks={count}'
                    ok = count == 1 if config.production else count == 0
                checks.append({'check': 'max_connection', 'ok': ok, 'note': note})
    except (ValueError, OSError, sqlite3.Error, httpx.HTTPError):
        print('Diagnostic failed; check configuration, database and network. Details suppressed to protect credentials.')
        raise SystemExit(1)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(c['ok'] for c in checks) else 1)


if __name__ == '__main__':
    main()
