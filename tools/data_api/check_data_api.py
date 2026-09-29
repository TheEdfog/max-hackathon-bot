"""Run DATA-API checks twice on an authorized isolated HTTPS verification deployment."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse
import httpx
import jsonschema
import yaml

VARIABLE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}')


def substitute(value, variables):
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    return VARIABLE.sub(lambda m: str(variables[m[1]]), value) if isinstance(value, str) else value


def execute(client, step, roles, variables, defaults):
    req = substitute(step.get('request', {}), variables)
    path = step['path']
    for key, value in req.get('path', {}).items():
        path = path.replace('{' + key + '}', str(value))
    if (not path.startswith('/api/') and path != '/health') or '://' in path or path.startswith('//'):
        raise ValueError('Only same-origin API paths are allowed')
    response = client.request(step['method'], path, params=req.get('query', {}),
                              timeout=max(0.1, min(120, step.get('timeoutMs', 20000) / 1000)),
                              headers={**defaults, **roles[step['role']], **req.get('headers', {})},
                              **({'json': req['body']} if 'body' in req else {}))
    expected = step['expected']
    if response.status_code not in expected['statusCodes']:
        raise ValueError(f"{step['id']}: unexpected HTTP {response.status_code}; response body redacted")
    if expected.get('contentType') and response.headers.get('content-type', '').split(';')[0] != expected['contentType']:
        raise ValueError(f"{step['id']}: unexpected content type")
    if expected.get('requiredFields') or expected.get('bodySchema') or step.get('extract'):
        body = response.json()
        if any(key not in body for key in expected.get('requiredFields', [])):
            raise ValueError(f"{step['id']}: required field missing")
        if 'bodySchema' in expected:
            if not jsonschema.Draft202012Validator(expected['bodySchema']).is_valid(body):
                raise ValueError(f"{step['id']}: body schema mismatch")
        for name, expression in step.get('extract', {}).items():
            if not re.fullmatch(r'\$\.[a-zA-Z_][a-zA-Z_0-9]*', expression):
                raise ValueError('Unsupported extraction expression')
            variables[name] = body[expression[2:]]


def run(client, document, roles):
    for _ in range(2):
        variables, completed = {}, set()
        try:
            for step in document['checks']:
                if not set(step.get('dependsOn', [])) <= completed:
                    raise ValueError('Unmet check dependency')
                execute(client, step, roles, variables, document['api']['defaultHeaders'])
                completed.add(step['id'])
        finally:
            for step in document.get('cleanup', []):
                if set(VARIABLE.findall(json.dumps(step))) <= variables.keys():
                    execute(client, step, roles, variables, document['api']['defaultHeaders'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', required=True)
    parser.add_argument('--manifest', default='DATA-API.yaml')
    parser.add_argument('--isolated-test-server', required=True, action='store_true')
    args = parser.parse_args()
    document = yaml.safe_load(Path(args.manifest).read_text(encoding='utf-8'))
    base = document['api']['baseUrl'].rstrip('/')
    url = urlparse(base)
    if url.scheme != 'https' or not url.hostname or url.hostname.endswith('.invalid') or url.username or url.password:
        raise SystemExit('A real HTTPS API address without URL credentials is required')
    accounts = json.loads(Path(args.credentials).read_text(encoding='utf-8'))
    with httpx.Client(base_url=base, timeout=20, follow_redirects=False) as client:
        roles = {'public': {}}
        for role in ('employer', 'candidate', 'other_employer'):
            if not accounts[role]['email'].endswith('@example.com'):
                raise SystemExit('Only synthetic @example.com accounts are allowed')
            response = client.post('/api/auth/login', json=accounts[role])
            if response.status_code != 200:
                raise SystemExit(f'Login failed for role {role}; HTTP {response.status_code}')
            roles[role] = {'Authorization': 'Bearer ' + response.json()['token']}
            me = client.get('/api/me', headers=roles[role])
            if me.status_code != 200 or me.json().get('max_connected') or not me.json().get('email', accounts[role]['email']).endswith('@example.com'):
                raise SystemExit('Only isolated synthetic identities without MAX binding are allowed')
        run(client, document, roles)
    print('PASS: two HTTPS DATA-API cycles with cleanup; credentials not printed.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, httpx.HTTPError) as exc:
        raise SystemExit(f'Verification failed ({type(exc).__name__}); inspect configuration privately.') from None
