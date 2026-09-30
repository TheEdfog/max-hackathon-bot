"""Offline release artifact checks. Never load .env files or contact MAX/clouds.

This does not run pytest, build Docker, verify a live deployment or certify a
hackathon submission. --submission additionally blocks a dirty checkout or an
unset HTTPS URL. Exit: 0 checks pass; 1 failed checks; 2 submission blockers.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit
from dotenv import dotenv_values
import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('README.md', 'Dockerfile', 'docker-compose.yml',
            'compose.production.yml', 'compose.ip.yml', '.env.review.example',
            'deploy/Caddyfile', 'deploy/Caddyfile.ip', 'requirements-hiring.lock',
            'requirements-hiring-test.lock', 'DATA-API.yaml', 'openapi.json',
            'docs/DEPLOYMENT.md', 'docs/HR-INTEGRATION.md', 'docs/DATA-POLICY.md')
ENV_TEMPLATES = {'.env.hiring.example', '.env.review.example'}


def safe_release_name(name):
    name = name.lower()
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        return False
    if any(part in {'data', '.git', '.venv', 'test-results'} for part in path.parts):
        return False
    if path.name.startswith('.env') and name not in ENV_TEMPLATES:
        return False
    if path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.log'}:
        return False
    if name.startswith(('storage/attachments/', 'storage/generated/', 'storage/temp/', 'storage/vacancy_raw/')):
        return path.name == '.gitkeep'
    return True


def deployment_url_set(value):
    try:
        url = urlsplit(value)
        host = (url.hostname or '').rstrip('.').lower()
        if (url.scheme != 'https' or not host or url.username or url.password or url.port not in (None, 443)
                or url.query or url.fragment or url.path not in ('', '/', '/review') or '.' not in host
                or host.endswith(('.invalid', '.localhost', '.local', '.test', '.example'))
                or any(host == d or host.endswith('.' + d) for d in ('example.com', 'example.org', 'example.net'))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host))
    except (TypeError, ValueError):
        return False


def inspect_files(root, tracked):
    checks = []
    def add(name, ok, note):
        checks.append({'check': name, 'ok': bool(ok), 'note': note})
    missing = [name for name in REQUIRED if not (root / name).is_file() or name not in tracked]
    add('required_artifacts', not missing, 'Missing/untracked: ' + ', '.join(missing) if missing else 'Required source artifacts present')
    unsafe = [name for name in tracked if not safe_release_name(name)]
    # Deliberately output a count, not potentially sensitive private file names.
    add('publishable_paths', not unsafe, f'{len(unsafe)} disallowed paths; not a content-level secret audit')
    lines = (root / 'requirements-hiring.lock').read_text(encoding='utf-8').splitlines()
    pins = [line.strip().split(';', 1)[0].strip() for line in lines if line.strip() and not line.lstrip().startswith('#')]
    add('runtime_pins', pins and all(re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+', line) for line in pins),
        f'{len(pins)} runtime packages, exact versions required')
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in REQUIRED if (root / name).is_file()}
    return checks, hashes


def inspect_known_secrets(root, history=False):
    """Compare local .env.hiring values without printing secret contents."""
    values = dotenv_values(root / '.env.hiring')
    secrets = [value.encode() for key, value in values.items() if value and len(value) >= 8
               and any(part in key.upper() for part in ('TOKEN', 'SECRET', 'PASSWORD', 'EMPLOYER_CODE', 'API_KEY'))]
    names = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                           cwd=root, check=True, capture_output=True).stdout.decode().split('\0')
    findings = [name for name in set(names) - {''} if (root / name).is_file()
                and any(secret in (root / name).read_bytes() for secret in secrets)]
    history_hit = False
    if history and secrets:
        output = subprocess.run(['git', 'log', '--all', '-p', '--no-ext-diff'], cwd=root,
                                check=True, capture_output=True).stdout
        history_hit = any(secret in output for secret in secrets)
    return len(secrets), sorted(findings), history_hit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', action='store_true')
    parser.add_argument('--secrets', action='store_true', help='Compare .env.hiring values with publishable files')
    parser.add_argument('--history', action='store_true', help='Also inspect reachable Git text history')
    args = parser.parse_args()
    def run(command):
        return subprocess.run(command, cwd=ROOT, capture_output=True, encoding='utf-8', timeout=30,
                              env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    try:
        commit = run(['git', 'rev-parse', 'HEAD'])
        files = run(['git', 'ls-files', '-z'])
        status = run(['git', 'status', '--porcelain'])
        if any(r.returncode for r in (commit, files, status)):
            raise ValueError('Git unavailable')
        checks, hashes = inspect_files(ROOT, set(files.stdout.rstrip('\0').split('\0')))
        if args.secrets or args.history:
            count, findings, history_hit = inspect_known_secrets(ROOT, args.history)
            checks.append({'check': 'known_local_secrets', 'ok': not findings and not history_hit,
                           'note': (f'{count} known values checked; {len(findings)} publishable file(s) matched'
                                    + ('; Git history matched' if history_hit else ''))})
            if findings:
                print('Known local secret values occur in publishable files: ' + ', '.join(findings))
            if history_hit:
                print('A known local secret value occurs in reachable Git history; investigate privately.')
        for name, command in (
            ('data_api_schema', [sys.executable, 'tools/data_api/validate_data_api.py', 'DATA-API.yaml']),
            ('openapi_matches_code', [sys.executable, 'scripts/export_hiring_openapi.py', '--check']),
            ('dependency_consistency', [sys.executable, '-m', 'pip', 'check']),
        ):
            result = run(command)
            checks.append({'check': name, 'ok': result.returncode == 0, 'note': f'exit={result.returncode}'})
        manifest = yaml.safe_load((ROOT / 'DATA-API.yaml').read_text(encoding='utf-8'))
        blockers = []
        if status.stdout.strip():
            blockers.append('Uncommitted or untracked source changes: fix the submission commit first')
        if not deployment_url_set(manifest['api']['baseUrl']):
            blockers.append('DATA-API baseUrl still needs the public HTTPS deployment address')
        failed = any(not item['ok'] for item in checks)
        code = 1 if failed else (2 if args.submission and blockers else 0)
        print(json.dumps({'commit': commit.stdout.strip(), 'checks': checks, 'artifact_sha256': hashes,
                          'submission_blockers': blockers, 'exit_code': code,
                          'not_verified': ['pytest results', 'Docker build/start/restart', 'HTTPS reachability and remote DATA-API',
                                           'MAX client acceptance', 'presentation', 'content-level secrets']}, indent=2))
        return code
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, subprocess.TimeoutExpired):
        print('Release check could not complete. Check required files and local tools; diagnostic details suppressed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
