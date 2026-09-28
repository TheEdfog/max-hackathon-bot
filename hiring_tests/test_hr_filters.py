from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from hiring.db import Application
from test_product import client, register, job
from test_integrations import key, PREFIX, vacancy
from test_talent import RESUME, ALL


def test_external_lookup_is_read_only_and_company_scoped(client):
    owner, other = register(client, 'owner', 'employer'), register(client, 'other', 'employer')
    writer, _ = key(client, owner, ['jobs:read', 'jobs:write'])
    reader, _ = key(client, owner)
    foreign, _ = key(client, other, ['jobs:read', 'jobs:write'])
    path = PREFIX + '/jobs/by-external/ats/REQ-1'
    first = client.put(path, headers=writer, json=vacancy()).json()['id']
    assert client.get(path, headers=reader).json()['id'] == first
    assert client.get(path, headers=foreign).status_code == 404
    second = client.put(path, headers=foreign, json=vacancy()).json()['id']
    assert client.get(path, headers=foreign).json()['id'] == second != first
    client.put(PREFIX + '/jobs/by-external/other/REQ-2', headers=writer, json=vacancy()).raise_for_status()
    job(client, owner)
    assert [v['id'] for v in client.get(PREFIX + '/jobs?source=ats', headers=reader).json()['items']] == [first]
    assert client.get(PREFIX + '/jobs?source=missing', headers=reader).json()['items'] == []
    assert client.get(PREFIX + '/jobs?source=bad%20source', headers=reader).status_code == 422


def test_dates_pagination_and_withdrawal(client):
    owner, other = register(client, 'owner', 'employer'), register(client, 'other', 'employer')
    full, _ = key(client, owner, ALL)
    reader, _ = key(client, owner)
    foreign, _ = key(client, other, ALL)
    jid = job(client, owner)
    ids, candidates = [], []
    for index in range(3):
        candidate = register(client, 'candidate-' + str(index))
        candidates.append(candidate)
        result = client.post('/api/jobs/' + jid + '/apply', headers=candidate,
                             json={'name': 'Synthetic', 'resume': RESUME, 'consent': True})
        result.raise_for_status()
        ids.append(result.json()['id'])
    with client.app.state.factory() as db:
        for index, aid in enumerate(ids):
            db.get(Application, aid).created_at = datetime(2026, 9, 20 + index, 12, tzinfo=timezone.utc)
        db.commit()
    path = PREFIX + '/applications'
    assert client.get(path, headers=reader, params={'review_stage': 'shortlisted'}).status_code == 410
    assert client.get(path, headers=foreign).json()['items'] == []
    filters = {'limit': 2, 'job_id': jid}
    page = client.get(path, headers=full, params=filters).json()
    more = client.get(path, headers=full, params={**filters, 'after': page['next_cursor']}).json()
    assert {v['id'] for v in page['items'] + more['items']} == set(ids) and more['next_cursor'] is None
    dates = {'created_from': '2026-09-21T15:00:00+03:00', 'created_before': '2026-09-22T12:00:00Z'}
    assert [v['id'] for v in client.get(path, headers=reader, params=dates).json()['items']] == [ids[1]]
    client.delete('/api/applications/' + ids[1], headers=candidates[1]).raise_for_status()
    tombstones = client.get(path, headers=full, params={'status': 'withdrawn'}).json()['items']
    assert tombstones == [{'id': ids[1], 'job_id': jid, 'status': 'withdrawn', 'deleted': True}]


@pytest.mark.parametrize('params', [
    {'created_from': '2026-09-20T12:00:00'},
    {'created_before': 'not-a-date'},
    {'created_from': '2026-09-22T12:00:00Z', 'created_before': '2026-09-22T12:00:00Z'},
    {'created_from': '2026-09-23T12:00:00Z', 'created_before': '2026-09-22T12:00:00Z'},
])
def test_bad_filters_rejected(client, params):
    auth, _ = key(client, register(client, 'owner', 'employer'), ALL)
    assert client.get(PREFIX + '/applications', headers=auth, params=params).status_code == 422
