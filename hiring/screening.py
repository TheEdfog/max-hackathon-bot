"""Optional, job-related prompts. No ranking or inference about personal traits."""
PRESETS = [
    {'id': 'screen_motivation', 'label': 'Интерес к вакансии', 'text': 'Какие задачи этой вакансии вам интересны? Можно пропустить вопрос.'},
    {'id': 'screen_conditions', 'label': 'Ожидания и формат', 'text': 'Какой формат работы и уровень оплаты вам подходят? Если называете сумму, уточните валюту, период и до или после налогов. Можно пропустить вопрос.'},
    {'id': 'screen_availability', 'label': 'Срок выхода', 'text': 'Когда вы готовы приступить к работе и когда удобно обсудить вакансию? Укажите часовой пояс. Можно пропустить вопрос.'},
]


def screening_questions(ids):
    return [dict(question, kind='screening') for question in PRESETS if question['id'] in ids]
