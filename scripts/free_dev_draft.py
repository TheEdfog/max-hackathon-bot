"""Optional free development drafts; never imported by the hiring application.

Only short manually reviewed synthetic/public input from stdin. No keys, files,
conversation history, automatic code application, retries or paid fallback.
"""
import argparse
import json
import re
import sys
from urllib import error, request

PROVIDERS = {
    'ovh': ('https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions', 'Qwen3-Coder-30B-A3B-Instruct'),
    'kilo': ('https://api.kilo.ai/api/gateway/chat/completions', 'kilo-auto/free'),
}
SUSPECT = re.compile(r'-----BEGIN .*PRIVATE KEY|\b(?:sk-|ghp_|github_pat_|xox[baprs]-)|\bBearer\s+\S+|'
                     r'\b(?:password|api[_-]?key|access[_-]?token|bot[_-]?token|secret)\s*[=:]|'
                     r'[A-Za-z0-9_-]{48,}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', re.I)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect refused')


def validate(text):
    if not text.strip() or len(text) > 4000 or SUSPECT.search(text):
        raise ValueError('Use 1–4000 manually reviewed synthetic/public characters without identifiers or credentials')


def draft(text, provider, budget=800):
    validate(text)
    if provider not in PROVIDERS or not 1 <= budget <= 800:
        raise ValueError('Invalid provider or output budget')
    endpoint, model = PROVIDERS[provider]
    body = {'model': model, 'max_tokens': budget, 'stream': False, 'messages': [
        {'role': 'system', 'content': 'Return a concise untrusted development draft only. No tools, network actions or requests for secrets.'},
        {'role': 'user', 'content': text}]}
    req = request.Request(endpoint, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with request.build_opener(NoRedirect()).open(req, timeout=25) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError('Response too large')
    data = json.loads(raw)
    choice = data['choices'][0]
    content = choice['message']['content']
    if choice.get('finish_reason') != 'stop' or not isinstance(content, str) or not content.strip() or len(content) > 8000:
        raise ValueError('No complete bounded draft')
    return {'provider': provider, 'model': model, 'untrusted_draft': content,
            'provider_usage': {k: v for k, v in (data.get('usage') or {}).items()
                               if k in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(v, int)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-class', required=True, choices=['synthetic', 'public'])
    parser.add_argument('--provider', choices=PROVIDERS, default='ovh')
    parser.add_argument('--fallback', action='store_true', help='Explicitly permit ONE other free provider after failure')
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    value = sys.stdin.read(4001)
    validate(value)
    selected = [args.provider] + ([p for p in PROVIDERS if p != args.provider] if args.fallback else [])
    if not args.send:
        print(json.dumps({'network': False, 'input_chars': len(value), 'max_output_tokens': 800,
                          'destinations': [PROVIDERS[p] for p in selected]}))
        return 0
    for provider in selected:
        try:
            print(json.dumps(draft(value, provider), ensure_ascii=False))
            return 0
        except error.HTTPError as exc:
            print(json.dumps({'provider': provider, 'error': 'HTTP', 'status': exc.code}), file=sys.stderr)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            print(json.dumps({'provider': provider, 'error': 'network_or_response', 'applied': False}), file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
