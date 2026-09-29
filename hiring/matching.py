"""Find skill mentions and show their source. No scores or inferred competence."""
import re
from .requirements_parser import extract_requirements_locally, KNOWN_SKILL_ALIASES
from .skills import normalize_skill, SKILL_ALIASES

ALIASES = {}
for alias, canonical in SKILL_ALIASES.items():
    ALIASES.setdefault(canonical, set()).update((alias, canonical))
for canonical, aliases in KNOWN_SKILL_ALIASES.items():
    canonical = normalize_skill(canonical)
    ALIASES.setdefault(canonical, set()).update((*aliases, canonical))
ALIASES.setdefault('python', set()).update(('python', 'питон', 'питоне', 'питоном'))
ALIASES = {skill: sorted(values, key=lambda value: (-len(value), value)) for skill, values in ALIASES.items()}

# A whole ambiguous sentence goes to the recruiter. We deliberately do not try
# to attach negation to individual words or infer experience from related tools.
CAUTION = re.compile(
    r"\b(?:не|нет|без\s+опыта|хочу|планир\w*|слышал\w*|возможно|"
    r"no|not|never|don't|didn't|want\s+to|plan\s+to|maybe)\b", re.I)


def skill_pattern(skill):
    return re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(x) for x in ALIASES.get(skill, [skill])) + r')(?!\w)', re.I)


def fragments(text):
    return [part.strip() for part in re.split(r'[\n;]|(?<=[.!?])\s+', text) if part.strip()]


def extract(text):
    items = extract_requirements_locally(text)
    return [{'id': f'r{i}', 'skill': item['skill_norm'],
             'label': item.get('display_name') or item['skill_norm'],
             'type': item.get('type', 'must'), 'source': item.get('source_text', '')}
            for i, item in enumerate(items[:15])]


def evidence(resume, answers, requirements):
    sentences = fragments(resume)
    rows = []
    for req in requirements:
        pattern = skill_pattern(normalize_skill(req['skill']))
        mentions = [text for text in sentences if pattern.search(text)]
        answer = answers.get(req['id'], '')
        state = 'unknown'
        if mentions or answer:
            ambiguous = any(CAUTION.search(text) for text in mentions)
            if answer:
                ambiguous = ambiguous or bool(CAUTION.search(answer)) or not pattern.search(answer)
            state = 'review' if ambiguous else 'mentioned'
        rows.append({**req, 'state': state,
                     'snippets': list(dict.fromkeys(text[:700] for text in mentions))[:3],
                     'answer': answer})
    return {'requirements': rows, 'total': len(rows),
            **{state: sum(row['state'] == state for row in rows)
               for state in ('mentioned', 'review', 'unknown')}}


def questions(resume, requirements):
    pending = [row for row in evidence(resume, {}, requirements)['requirements'] if row['state'] != 'mentioned']
    pending.sort(key=lambda row: row['type'] != 'must')
    return [{'id': row['id'], 'label': row['label'],
             'text': f"Расскажите о своём опыте с {row['label']}: какую задачу решали и что делали лично? Если опыта нет, так и напишите."}
            for row in pending[:3]]
