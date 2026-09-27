"""Local-only functional evaluation of authorized interview material.

Reads the supplied transcript, never calls MAX/LLMs/HH, and never prints personal
texts. Uses three real vacancy briefs and one candidate's professional answers,
NOT a representative resume benchmark or hiring-quality accuracy measurement.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['HIRING_DATABASE_URL'] = 'sqlite://'
os.environ['MAX_BOT_TOKEN'] = ''
os.environ['HIRING_ENV'] = 'development'

from fastapi.testclient import TestClient
from hiring.config import Config
from hiring.main import create_app
from hiring.matching import extract


def material(text):
    blocks = re.split(r'(?m)^(Edfog|ГигаРекрутер):\s*\n', text)
    candidate, briefs = [], []
    for index in range(1, len(blocks), 2):
        role, body = blocks[index], blocks[index + 1].strip()
        if role == 'Edfog' and re.search(r'\b(?:SQL|ETL|ELT|Spark|PySpark|Git|Airflow|Hadoop|Kafka|DQ)\b', body, re.I):
            candidate.append(body)
        elif role == 'ГигаРекрутер' and 'Здравствуйте' in body:
            body = body[body.rfind('Здравствуйте'):]
            start = body.find('Основные задачи')
            if start < 0:
                start = body.find('Наша команда')
            if start >= 0:
                briefs.append(body[start:].split('Будет удобно')[0].strip())
    resume = '\n\n'.join(dict.fromkeys(candidate))
    if not 40 <= len(resume) <= 20000 or len(briefs) != 3:
        raise ValueError('Expected three vacancy briefs and bounded professional responses; no data sent')
    return briefs, resume


def evaluate(path):
    briefs, resume = material(path.read_text(encoding='utf-8-sig'))
    config = Config(database_url=f'sqlite:///file:eval_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true',
                    secret='synthetic-evaluation-only-1234567890', demo=False, worker=False, bot_token='', employer_code='')
    results = []
    with TestClient(create_app(config)) as client:
        def account(name, role):
            response = client.post('/api/auth/register', json={'email': name + '@example.com', 'name': name,
                'password': 'synthetic-eval-only', 'role': role, 'company': 'Local evaluation'})
            response.raise_for_status()
            return {'Authorization': 'Bearer ' + response.json()['token']}
        owner, candidate = account('employer', 'employer'), account('candidate', 'candidate')
        for index, description in enumerate(briefs):
            requirements = extract(description)
            expected = ({'sql', 'spark', 'hadoop', 'greenplum', 'etl'} if index == 0 else {'sql', 'etl'})
            missing = expected - {r['skill'] for r in requirements}
            job = client.post('/api/jobs', headers=owner, json={'title': f'Local real vacancy brief {index + 1}',
                'description': description, 'requirements': requirements,
                'screening_questions': ['screen_conditions', 'screen_availability']})
            job.raise_for_status()
            jid = job.json()['id']
            response = client.post('/api/jobs/' + jid + '/apply', headers=candidate,
                json={'name': 'Local authorized candidate', 'resume': resume, 'consent': True})
            response.raise_for_status()
            app = response.json()
            # Validate provenance, not a claim that stated skills are verified.
            assert all(snippet in resume for row in app['assessment']['requirements'] for snippet in row['snippets'])
            assert len({q['id'] for q in app['questions']}) == len(app['questions']) <= 6
            # Synthetic skipped answers test workflow; never invent this person's answers.
            response = client.post('/api/applications/' + app['id'] + '/answers', headers=candidate,
                json={'answers': {q['id']: 'Пропускаю уточнение, сведений недостаточно.' for q in app['questions']}})
            response.raise_for_status()
            assert response.json()['status'] == 'ready'
            client.post('/api/applications/' + app['id'] + '/invite', headers=owner,
                        json={'message': 'Synthetic local invitation, no real interview arranged.'}).raise_for_status()
            client.post('/api/applications/' + app['id'] + '/confirm', headers=candidate).raise_for_status()
            client.delete('/api/applications/' + app['id'], headers=candidate).raise_for_status()
            results.append({'vacancy_brief': index + 1, 'requirements': len(requirements),
                            'missing_expected_technical_terms': sorted(missing),
                            'verbatim_evidence': True, 'workflow': 'passed', 'questions': len(app['questions'])})
    return {'source': 'User-authorized private interview; local only', 'vacancy_briefs': len(briefs),
            'real_candidate_profiles': 1, 'external_transfers': 0, 'representative_accuracy_claim': False,
            'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transcript', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.transcript), ensure_ascii=False, indent=2))
