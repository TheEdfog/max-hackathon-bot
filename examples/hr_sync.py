"""Small pull-integration adapter; run on the HR server, never in a browser."""
import argparse
import json
import os
import re
from urllib.parse import urlparse
import httpx

PREFIX = '/api/integrations/v1'


def validate_page(page, after):
    if type(after) is not int or not 0 <= after <= 2**63 - 1 or not isinstance(page, dict):
        raise ValueError('Invalid cursor/page')
    rows, cursor, more = page.get('items'), page.get('next_cursor'), page.get('has_more')
    if not isinstance(rows, list) or len(rows) > 200 or type(more) is not bool:
        raise ValueError('Invalid page fields')
    if not isinstance(cursor, str) or not re.fullmatch(r'0|[1-9][0-9]{0,18}', cursor):
        raise ValueError('Invalid next cursor')
    last = after
    for row in rows:
        seq = row.get('seq') if isinstance(row, dict) else None
        if type(seq) is not int or not last < seq <= 2**63 - 1:
            raise ValueError('Events must be strictly increasing; gaps are allowed')
        last = seq
    if int(cursor) != last or (not rows and more):
        raise ValueError('Inconsistent page cursor')
    return rows, last, more


def sync_page(client, after, apply_change):
    response = client.get(PREFIX + '/events', params={'after': str(after), 'limit': 100})
    response.raise_for_status()
    rows, next_cursor, more = validate_page(response.json(), after)
    for row in rows:
        resource_id, kind = row.get('resource_id', ''), row.get('type', '')
        if not isinstance(resource_id, str) or not re.fullmatch(r'[0-9a-f]{32}', resource_id):
            raise ValueError('Invalid resource ID')
        if not isinstance(kind, str):
            raise ValueError('Invalid event type')
        if kind.startswith('job.'):
            resource = 'jobs'
        elif kind.startswith('application.'):
            resource = 'applications'
        else:
            raise ValueError('Unknown event type; update the adapter before advancing')
        result = client.get(f'{PREFIX}/{resource}/{resource_id}')
        if result.status_code == 404:
            document = {'id': resource_id, 'deleted': True}
        else:
            result.raise_for_status()
            document = result.json()
        # The HR implementation must upsert/delete idempotently by document ID.
        # On failure the cursor is NOT returned/advanced; retry safely replays.
        apply_change(resource, document, row['seq'])
    return next_cursor, more


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after', type=int, default=0)
    args = parser.parse_args()
    base, token = os.environ.get('INTEGRATION_API_URL', ''), os.environ.get('INTEGRATION_KEY', '')
    url = urlparse(base)
    local = url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')
    if (not token or not url.hostname or (url.scheme != 'https' and not local)
            or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
        raise SystemExit('Set HTTPS origin and a scoped key in the environment; HTTP only on loopback')
    def summary(resource, document, seq):
        print(json.dumps({'seq': seq, 'resource': resource, 'id': document['id'],
                          'deleted': document.get('deleted', False), 'status': document.get('status')}))
    with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + token},
                      timeout=15, follow_redirects=False) as client:
        cursor, more = sync_page(client, args.after, summary)
    print(json.dumps({'next_cursor': str(cursor), 'has_more': more,
                      'note': 'Demo only: replace summary() with transactional HR upsert/delete before saving cursor'}))


if __name__ == '__main__':
    try:
        main()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit('Sync failed: ' + type(exc).__name__ + '; cursor not advanced') from None
