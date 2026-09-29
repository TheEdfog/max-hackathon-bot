"""Cache normalized resume mentions and show direct or related evidence."""
import hashlib
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

# A whole ambiguous sentence goes to the recruiter. Related tools are clues,
# not proof that the candidate has the requested skill.
CAUTION = re.compile(
    r"\b(?:не|нет|без\s+опыта|хочу|планир\w*|слышал\w*|возможно|"
    r"no|not|never|don't|didn't|want\s+to|plan\s+to|maybe)\b", re.I)
NORMALIZATION_VERSION = 1
RELATED_SKILLS = {'sql': ('postgresql',)}


def skill_pattern(skill):
    return re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(x) for x in ALIASES.get(skill, [skill])) + r')(?!\w)', re.I)


def fragments(text):
    return [part.strip() for part in re.split(r'[\n;]|(?<=[.!?])\s+', text) if part.strip()]


def resume_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def normalize_resume(text):
    """Store canonical skill names and sentence positions, never duplicate resume text."""
    sentences = fragments(text)
    mentions = {}
    for skill in ALIASES:
        pattern = skill_pattern(skill)
        positions = [index for index, sentence in enumerate(sentences) if pattern.search(sentence)]
        if positions:
            mentions[skill] = positions
    return {'version': NORMALIZATION_VERSION,
            'resume_sha256': resume_digest(text),
            'mentions': mentions}


def extract(text):
    items = extract_requirements_locally(text)
    return [{'id': f'r{i}', 'skill': item['skill_norm'],
             'label': item.get('display_name') or item['skill_norm'],
             'type': item.get('type', 'must'), 'source': item.get('source_text', '')}
            for i, item in enumerate(items[:15])]


def evidence(resume, answers, requirements, normalized_skills=None):
    sentences = fragments(resume)
    digest = resume_digest(resume)
    if (not isinstance(normalized_skills, dict)
            or normalized_skills.get('version') != NORMALIZATION_VERSION
            or normalized_skills.get('resume_sha256') != digest
            or not isinstance(normalized_skills.get('mentions'), dict)):
        normalized_skills = normalize_resume(resume)
    mentions_by_skill = normalized_skills.get('mentions', {})
    rows = []
    for req in requirements:
        skill = normalize_skill(req['skill'])
        indexes = mentions_by_skill.get(skill, [])
        related = {source: mentions_by_skill[source] for source in RELATED_SKILLS.get(skill, ())
                   if mentions_by_skill.get(source)}
        indirect_sources = sorted(related)
        direct_mention = bool(indexes)
        if not direct_mention and related:
            indexes = sorted({index for positions in related.values() for index in positions})
        mentions = [sentences[index] for index in indexes if isinstance(index, int) and 0 <= index < len(sentences)]
        answer = answers.get(req['id'], '')
        state = 'unknown'
        if answer:
            pattern = skill_pattern(skill)
            ambiguous = any(CAUTION.search(text) for text in mentions)
            ambiguous = ambiguous or bool(CAUTION.search(answer)) or not pattern.search(answer)
            state = 'review' if ambiguous else 'mentioned'
        elif direct_mention:
            state = 'review' if any(CAUTION.search(text) for text in mentions) else 'mentioned'
        elif related:
            state = 'review' if any(CAUTION.search(text) for text in mentions) else 'indirect'
        explicit_answer = bool(answer and skill_pattern(skill).search(answer) and not CAUTION.search(answer))
        rows.append({**req, 'state': state,
                     'indirect_sources': indirect_sources if not direct_mention and not explicit_answer else [],
                     'snippets': list(dict.fromkeys(text[:700] for text in mentions))[:3],
                     'answer': answer})
    return {'requirements': rows, 'total': len(rows),
            **{state: sum(row['state'] == state for row in rows)
               for state in ('mentioned', 'review', 'indirect', 'unknown')}}


def questions(resume, requirements, normalized_skills=None):
    pending = [row for row in evidence(resume, {}, requirements, normalized_skills)['requirements'] if row['state'] != 'mentioned']
    pending.sort(key=lambda row: row['type'] != 'must')
    return [{'id': row['id'], 'label': row['label'],
             'text': f"Расскажите о своём опыте с {row['label']}: какую задачу решали и что делали лично? Если опыта нет, так и напишите."}
            for row in pending[:3]]
