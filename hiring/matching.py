"""Explainable extraction. Evidence is a source statement, never verified competence."""
import re
from apps.web.services.vacancy_parser_service import extract_requirements_locally
from core.utils import normalize_skill

NEGATION = re.compile(r"\b(?:нет|не\s+(?:работал\w*|использовал\w*|знаю|знаком\w*|имею)|без\s+опыта|не\s+было|не\s+доводилось)\b", re.I)
ALIASES = {"postgresql": ["postgresql", "postgres", "постгрес"], "javascript": ["javascript", "js"], "python": ["python", "питон"], "git": ["git", "гит"], "fastapi": ["fastapi", "fast api"], "docker": ["docker", "докер"], "sql": ["sql"]}


def clauses(text):
    # A negation about Docker must not negate Python in the preceding clause.
    return [s.strip() for s in re.split(r"[\n;]|(?<=[.!?])\s+|,?\s+(?:но|а|однако|зато)\s+", text, flags=re.I) if s.strip()]


def extract(text):
    items = extract_requirements_locally(text)
    return [{"id": f"r{i}", "skill": item["skill_norm"], "label": item.get("display_name") or item["skill_norm"], "type": item.get("type", "must"), "source": item.get("source_text", "")} for i, item in enumerate(items[:15])]


def evidence(resume, answers, requirements):
    rows = []
    sentences = clauses(resume)
    for req in requirements:
        skill = normalize_skill(req["skill"])
        aliases = ALIASES.get(skill, [skill])
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(x) for x in aliases) + r")(?!\w)", re.I)
        snippets = [s[:700] for s in sentences if pattern.search(s)]
        positive = [s for s in snippets if not NEGATION.search(s)]
        negative = [s for s in snippets if NEGATION.search(s)]
        answer = answers.get(req["id"], "")
        state = "mentioned" if positive else "unknown"
        if negative:
            state = "conflict" if positive else "negative"
        if answer:
            relevant = [s for s in clauses(answer) if pattern.search(s)]
            answer_negative = any(NEGATION.search(s) for s in relevant) if relevant else bool(NEGATION.search(answer))
            answer_positive = any(not NEGATION.search(s) for s in relevant)
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
    return [{"id": r["id"], "label": r["label"], "text": f"Расскажите о своём опыте с {r['label']}: в каком проекте, какую задачу вы решали и что делали лично? Если опыта нет, так и напишите."} for r in pending if r["state"] in ("unknown", "conflict")][:3]
