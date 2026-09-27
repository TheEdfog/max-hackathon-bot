"""Vadim's explainable weighted evidence formula, adapted to flat MAX resumes.

No trained model, network, automatic ranking or hiring decision. Contradictions,
negation and uncertainty override keyword weights. GitHub enrichments are NOT
inputs. A missing statement is not a demonstrated lack of competence.
"""
import re
from .matching import evidence, skill_pattern, statements, RELATED
from core.utils import normalize_skill

VERSION = 'vadim-evidence-adapted-1'
NOTICE = 'Это покрытие требований сведениями, не оценка личности, проверенный уровень или вероятность найма. Решение принимает работодатель.'
ACTION = re.compile(r'\b(?:разработ\w*|созда\w*|написа\w*|внедр\w*|управля\w*|провод\w*|пров[её]л\w*|организ\w*|собра\w*|анализир\w*|built|implemented|managed|delivered|designed)\b', re.I)


def compatibility(resume, answers, requirements):
    unique = {}
    for req in requirements:
        skill = normalize_skill(req['skill'])
        # A duplicated nice item must not weaken an explicit must requirement.
        if skill not in unique or req.get('type', 'must') == 'must':
            unique[skill] = {**req, 'skill': skill, 'type': req.get('type', 'must')}
    rows = evidence(resume, answers, list(unique.values()))['requirements']
    result = []
    for row in rows:
        skill = row['skill']
        pattern = skill_pattern(skill)
        direct = row['state'] in ('mentioned', 'answered')
        relevant_text = resume + '\n' + answers.get(row['id'], '')
        positives = list(dict.fromkeys(s for s, state in statements(relevant_text) if state == 'mentioned'))
        direct_snippets = [s for s in positives if pattern.search(s)]
        related = [(source, s) for source in RELATED.get(skill, ()) for s in positives if skill_pattern(source).search(s)]
        eligible = row['state'] in ('mentioned', 'answered', 'inferred')
        related_match = bool(related) and eligible
        # Count distinct supporting statements, not repeated words pasted in a list.
        hits = len(set(direct_snippets + [s for _, s in related])) if eligible else 0
        context = (1.0 if any(ACTION.search(s) for s in direct_snippets) else 0.5) if direct else 0.0
        components = {'exact': 0.65 if direct else 0.0, 'related': 0.35 if related_match else 0.0,
                      'text': round(0.25 * min(hits / 3, 1), 4), 'context': round(0.10 * context, 4)}
        score = min(1.0, sum(components.values())) if eligible else 0.0
        if row['state'] == 'negative':
            followup = 'Кандидат сообщил об отсутствии опыта. Уточните, допустимо ли обучение для этой роли.'
        elif row['state'] == 'conflict':
            followup = 'Уточните противоречие между исходным текстом и ответами.'
        elif row['state'] in ('unknown', 'review', 'inferred'):
            followup = 'Попросите конкретный пример: задача, личное действие и результат. Отсутствие сведений не означает отсутствие навыка.'
        else:
            followup = 'При необходимости проверьте пример на интервью или профильным заданием.'
        result.append({'id': row['id'], 'skill': skill, 'label': row['label'], 'type': row['type'],
                       'state': row['state'], 'evidence_percent': round(score * 100, 1),
                       'components': components, 'supporting_statements': hits, 'context_score': context,
                       'snippets': row['snippets'], 'answer': row['answer'], 'related': row['related'],
                       'followup': followup})
    def bucket(kind):
        values = [r['evidence_percent'] for r in result if r['type'] == kind]
        return (round(sum(values) / len(values), 1) if values else None), len(values)
    must, must_count = bucket('must')
    nice, nice_count = bucket('nice')
    total = round(0.75 * must + 0.25 * nice, 1) if must_count and nice_count else (must if must_count else nice)
    return {'method': VERSION, 'total_pct': total, 'must_pct': must, 'nice_pct': nice,
            'must_count': must_count, 'nice_count': nice_count, 'requirements': result, 'notice': NOTICE,
            'formula': 'min(1, 0.65*exact + 0.35*related + 0.25*min(statements/3,1) + 0.10*context); 75% must + 25% nice when both exist',
            'limitations': ['Flat resume: action wording approximates project context; no verified structured profile.',
                            'Distinct statements replace raw keyword frequency. Negative/conflicting/uncertain evidence scores zero.',
                            'Soft skills are self-reports only. No LLM, trained model, automatic ranking or decision.']}
