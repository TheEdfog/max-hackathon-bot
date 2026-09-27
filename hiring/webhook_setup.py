"""Explicit future webhook registration; never alters subscriptions without --apply."""
import argparse
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
from .config import Config
from .max_client import tls_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv('.env.hiring')
    config = Config()
    config.validate()
    url = urlparse(config.public_url)
    if url.scheme != 'https' or not url.hostname or url.port not in (None, 443) or url.hostname.endswith('.invalid'):
        raise SystemExit('Set a real PUBLIC_URL with HTTPS on port 443')
    if not config.bot_token or len(config.webhook_secret) < 24:
        raise SystemExit('Set MAX_BOT_TOKEN and MAX_WEBHOOK_SECRET (24+ characters) privately')
    endpoint = config.public_url + '/api/max/webhook'
    if not args.apply:
        print('Dry run: register webhook at', endpoint, 'for bot_started, message_created, message_callback. No network changes.')
        return
    # Verify availability without sending credentials to the public URL.
    with httpx.Client(timeout=15, follow_redirects=False) as probe:
        if probe.get(config.public_url + '/health').status_code != 200:
            raise SystemExit('Public health check failed; no subscription change')
    with httpx.Client(base_url=config.max_api_url, headers={'Authorization': config.bot_token}, verify=tls_context(config.max_api_url), timeout=20) as api:
        response = api.get('/subscriptions')
        if response.status_code != 200:
            raise SystemExit(f'Cannot inspect subscriptions: HTTP {response.status_code}')
        if any(x.get('url') != endpoint for x in response.json().get('subscriptions', [])):
            raise SystemExit('A different webhook exists; review it manually before changing delivery')
        response = api.post('/subscriptions', json={'url': endpoint, 'secret': config.webhook_secret,
                                                   'update_types': ['bot_started', 'message_created', 'message_callback']})
        if response.status_code != 200 or response.json().get('success') is False:
            raise SystemExit(f'Webhook registration failed: HTTP {response.status_code}')
    print('Webhook registered. Verify a real private-dialog cycle in MAX.')


if __name__ == '__main__':
    main()
