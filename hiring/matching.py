"""Explainable extraction. Evidence is a source statement, never verified competence."""
import re
from apps.web.services.vacancy_parser_service import extract_requirements_locally
from core.utils import normalize_skill

NEGATION = re.compile(r"\b(?:нет|не\s+(?:работал\w*|использовал\w*|изучал\w*|владе\w*|освоил\w*|знаю|знаком\w*|имею)|без\s+опыта|не\s+было|не\s+доводилось|never|no\s+experience|(?:do\s+not|don't|did\s+not|didn't)\s+(?:know|use|work))\b", re.I)
UNCERTAIN = re.compile(r"\b(?:хочу|планир\w*|слышал\w*|возможно|не\s+уверен\w*|не\s+только|want\s+to|plan\s+to|maybe|not\s+only)\b", re.I)
PREDICATE = re.compile(r"\b(?:работ\w*|использ\w*|изуч\w*|владе\w*|осво\w*|зна\w*|сделал\w*|написал\w*|создал\w*|разработал\w*|used?|know|worked?|built)\b", re.I)
ALIASES = {"postgresql": ["postgresql", "postgres", "постгрес"], "javascript": ["javascript", "js"], "python": ["python", "питон", "питоне", "питоном"], "git": ["git", "гит"], "fastapi": ["fastapi", "fast api"], "docker": ["docker", "докер"], "sql": ["sql"]}
ALIASES.update({'spark': ['spark', 'pyspark'], 'etl': ['etl', 'elt'],
                'data quality': ['data quality', 'dq', 'качество данных', 'качества данных']})


def clauses(text):
    # A negation about Docker must not negate Python in the preceding clause.
    return [s.strip() for s in re.split(r"[\n;]|(?<=[.!?])\s+|,?\s+(?:но|а|однако|зато)\s+", text, flags=re.I) if s.strip()]


def statements(text):
    """Conservative clause scope; snippets remain verbatim, never rewritten as proof."""
    result = []
    for sentence in clauses(text):
        pieces = [p.strip() for p in re.split(r",|\s+(?:и|and|but)\s+", sentence, flags=re.I) if p.strip()]
        prior = None
        for index, piece in enumerate(pieces):
            state = 'review' if UNCERTAIN.search(piece) else ('negative' if NEGATION.search(piece) else 'mentioned')
            if state == 'mentioned' and prior == 'review':
                state = 'review'
            elif state == 'mentioned' and not PREDICATE.search(piece):
                if prior == 'negative' or (index + 1 < len(pieces) and NEGATION.search(pieces[index + 1])):
                    state = 'negative'
            result.append((piece, state))
            prior = state
    return result


def extract(text):
    items = extract_requirements_locally(text)
    return [{"id": f"r{i}", "skill": item["skill_norm"], "label": item.get("display_name") or item["skill_norm"], "type": item.get("type", "must"), "source": item.get("source_text", "")} for i, item in enumerate(items[:15])]


def evidence(resume, answers, requirements):
    rows = []
    sentences = statements(resume)
    for req in requirements:
        skill = normalize_skill(req["skill"])
        aliases = ALIASES.get(skill, [skill])
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(x) for x in aliases) + r")(?!\w)", re.I)
        matches = [(s, state) for s, state in sentences if pattern.search(s)]
        snippets = [s[:700] for s, _ in matches]
        positive = [s for s, state in matches if state == 'mentioned']
        negative = [s for s, state in matches if state == 'negative']
        uncertain = any(state == 'review' for _, state in matches)
        answer = answers.get(req["id"], "")
        state = "mentioned" if positive else "unknown"
        if negative:
            state = "conflict" if positive else "negative"
        elif uncertain:
            state = 'review'
        if answer:
            relevant = [(s, value) for s, value in statements(answer) if pattern.search(s)]
            answer_negative = any(value == 'negative' for _, value in relevant) if relevant else bool(NEGATION.search(answer))
            answer_positive = any(value == 'mentioned' for _, value in relevant)
            if answer_negative:
                state = "conflict" if positive or answer_positive else "negative"
            elif answer_positive:
                state = "conflict" if negative else "answered"
            else:
                state = "review"
        rows.append({**req, "state": state, "snippets": snippets[:3], "answer": answer})
    stated = sum(row["state"] in ("mentioned", "answered") for row in rows)
    return {"requirements": rows, "covered": stated, "total": len(rows), "coverage": round(100 * stated / len(rows)) if rows else 0, "unknown": sum(row["state"] in ("unknown", "review") for row in rows), "conflicts": sum(row["state"] == "conflict" for row in rows)}


def questions(resume, requirements):
    rows = evidence(resume, {}, requirements)["requirements"]
    pending = sorted(rows, key=lambda r: (r["type"] != "must", r["state"] == "mentioned"))
    return [{"id": r["id"], "label": r["label"], "text": f"Расскажите о своём опыте с {r['label']}: в каком проекте, какую задачу вы решали и что делали лично? Если опыта нет, так и напишите."} for r in pending if r["state"] in ("unknown", "conflict", "review")][:3]
