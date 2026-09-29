"""Optional, job-related prompts. No ranking or inference about personal traits."""
import re
from datetime import date

PRESETS = [
    {'id': 'screen_motivation', 'label': 'Интерес к вакансии', 'text': 'Какие задачи этой вакансии вам интересны? Можно пропустить вопрос.'},
    {'id': 'screen_conditions', 'label': 'Ожидания и формат', 'text': 'Какой формат работы и уровень оплаты вам подходят? Если называете сумму, уточните валюту, период и до или после налогов. Можно пропустить вопрос.'},
    {'id': 'screen_availability', 'label': 'Срок выхода', 'text': 'Когда вы готовы приступить к работе и когда удобно обсудить вакансию? Укажите часовой пояс. Можно пропустить вопрос.'},
]

_DATE = r'(?:(?:\d{4}[-/.](?:0?[1-9]|1[0-2]))|(?:0?[1-9]|1[0-2])[-/.]\d{4}|\d{4})'
_CURRENT = r'(?:по\s+)?(?:н\.?\s*в\.?|настояще[ем]\s+врем[яи]|текущ(?:ий|ая|ее)|present|current)'
_PERIOD = re.compile(rf'(?P<start>{_DATE})\s*(?:[-–—]|\bпо\b|\bto\b)\s*(?P<end>{_DATE}|{_CURRENT})', re.I)


def _month(value, end=False):
    parts = re.split(r'[-/.]', value)
    if len(parts) == 1:
        year, month = int(parts[0]), 12 if end else 1
    elif len(parts[0]) == 4:
        year, month = int(parts[0]), int(parts[1])
    else:
        month, year = int(parts[0]), int(parts[1])
    return year * 12 + month


def recent_timeline_question(resume, today=None):
    """Ask once about a substantial recent, undocumented period; never infer its cause."""
    today = today or date.today()
    periods = []
    for line in resume.splitlines():
        for match in _PERIOD.finditer(line):
            start = _month(match.group('start'))
            end_text = match.group('end')
            if re.fullmatch(_CURRENT, end_text, re.I):
                return None
            end = _month(end_text, end=True)
            if start <= end <= today.year * 12 + today.month:
                periods.append((start, end))
    if not periods:
        return None

    latest_end = max(end for _, end in periods)
    current_month = today.year * 12 + today.month
    gap_start = max(latest_end + 1, current_month - 17)
    if current_month - gap_start + 1 < 6:
        return None

    start_year, start_month = divmod(gap_start - 1, 12)
    start_month += 1
    return {
        'id': 'recent_timeline',
        'label': 'Период в резюме',
        'kind': 'timeline',
        'text': (f'В резюме не указан профессиональный опыт с {start_month:02d}.{start_year} '
                 f'по {today.month:02d}.{today.year}. Хотите уточнить этот период - например, '
                 'работой, учёбой или проектом? Можно пропустить. Причины личного характера '
                 'указывать не нужно; отсутствие ответа само по себе не влияет на решение.'),
    }


def screening_questions(ids):
    return [dict(question, kind='screening') for question in PRESETS if question['id'] in ids]
