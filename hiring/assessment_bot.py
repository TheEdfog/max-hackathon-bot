from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from .assessments import TestBody, create_test, owned_test, snapshot
from .chat_ui import PAGE_SIZE, page_number
from .db import Application, AssessmentTemplate, Job


def handle_tests(db, user, session, text, reply):
    state, parts = dict(session.state), text.split()
    command = parts[0] if parts else ''
    buttons = [('Библиотека тестов', '/tests'), ('Меню', '/help')]
    if command in ('/tests', '/job-test'):
        choosing = command == '/job-test' and state.get('step') == 'job_review'
        if command == '/job-test' and not choosing:
            reply('Выберите тест на шаге проверки новой вакансии.', buttons)
            return True
        page = page_number(parts[1] if len(parts) > 1 else 0)
        rows = list(db.scalars(select(AssessmentTemplate).where(AssessmentTemplate.owner_id == user.id,
                    AssessmentTemplate.active == True).order_by(AssessmentTemplate.id).offset(page * PAGE_SIZE).limit(PAGE_SIZE + 1)))
        actions = [(r.title, ('/use-test ' if choosing else '/test-template ') + r.id) for r in rows[:PAGE_SIZE]]
        if page:
            actions.append(('⬅️ Назад', f'{command} {page - 1}'))
        if len(rows) > PAGE_SIZE:
            actions.append(('Далее ➡️', f'{command} {page + 1}'))
        actions.append(('Создать тест', '/newtest'))
        if choosing:
            actions.append(('Без теста', '/use-test none'))
        reply('Выберите тест из библиотеки.' if rows else 'В библиотеке пока нет тестов. Можно создать до 3 вопросов.', actions, bind=choosing)
    elif command == '/newtest':
        session.state = {'step': 'test_title', **({'return_draft': state} if state.get('step') == 'job_review' else {})}
        reply('Название теста, например «SQL для аналитика».', [('Отмена', '/cancel')])
    elif state.get('step') == 'test_title' and not command.startswith('/'):
        if not 3 <= len(text.strip()) <= 120:
            reply('Название: от 3 до 120 символов.')
        else:
            session.state = {**state, 'step': 'test_questions', 'title': text.strip()}
            reply('Пришлите 1-3 задания, каждое с новой строки, от 10 до 800 символов. '
                  'Можно добавить критерии для работодателя после ||. Например:\n'
                  'Как найти дубликаты в SQL? || GROUP BY и HAVING\n'
                  'Критерии кандидат не увидит. Проверяете ответы вы, без автоматического балла.')
    elif state.get('step') == 'test_questions' and not command.startswith('/'):
        try:
            values = [line.partition('||') for line in text.splitlines() if line.strip()]
            body = TestBody(title=state['title'], questions=[{'text': q.strip(), 'rubric': rubric.strip()} for q, _, rubric in values])
            row = create_test(db, user, body)
            draft = state.get('return_draft')
            session.state = draft or {}
            actions = [('Добавить к вакансии', '/use-test ' + row.id)] if draft else buttons
            reply(f'Тест «{row.title}» сохранён. Заданий: {len(row.questions)}.', actions, bind=bool(draft))
        except (ValidationError, HTTPException):
            reply('Нужно 1-3 разных задания по 10-800 символов. Критерии после || - до 1500 символов. Лимит библиотеки: 100 тестов.')
    elif command in ('/test-template', '/archive-test', '/use-test'):
        try:
            identifier = parts[1] if len(parts) > 1 else ''
            row = None if command == '/use-test' and identifier == 'none' else owned_test(db, identifier, user)
            if command == '/use-test':
                if state.get('step') != 'job_review':
                    raise HTTPException(409, 'Сначала перейдите к проверке новой вакансии.')
                session.state = {**state, 'test_questions': snapshot(row) if row else []}
                from .employer_bot import review_buttons
                reply('Тест для вакансии: ' + (row.title if row else 'без теста') +
                      '.\nКопия вопросов сохранится при публикации.', review_buttons(), bind=True)
            elif command == '/archive-test':
                row.active = False
                reply('Тест убран из библиотеки. В опубликованных вакансиях вопросы не изменились.', buttons)
            else:
                index = min(page_number(parts[2] if len(parts) > 2 else 0), len(row.questions) - 1)
                q = row.questions[index]
                actions = buttons + [('В архив', '/archive-test ' + row.id)]
                if index:
                    actions.append(('⬅️ Назад', f'/test-template {row.id} {index - 1}'))
                if index + 1 < len(row.questions):
                    actions.append(('Далее ➡️', f'/test-template {row.id} {index + 1}'))
                reply(f"{row.title} · задание {index + 1}/{len(row.questions)}\n\n{q['text']}\n\nКритерии: {q.get('rubric') or 'не указаны'}", actions)
        except HTTPException as exc:
            reply(str(exc.detail), buttons)
    elif command == '/test-answers':
        row = db.get(Application, parts[1]) if len(parts) > 1 else None
        if not row or row.status == 'withdrawn' or db.get(Job, row.job_id).owner_id != user.id:
            reply('Отклик не найден.')
        else:
            items = [q for q in row.questions if q.get('kind') == 'assessment']
            index = min(page_number(parts[2] if len(parts) > 2 else 0), max(0, len(items) - 1))
            actions = [('К карточке', '/view ' + row.id)]
            if index:
                actions.append(('⬅️ Назад', f'/test-answers {row.id} {index - 1}'))
            if index + 1 < len(items):
                actions.append(('Далее ➡️', f'/test-answers {row.id} {index + 1}'))
            if items:
                q = items[index]
                # Separate messages avoid silently truncating a long candidate answer.
                reply(f"{q['label']}: {q['text']}\n\nОтвет:\n{row.answers.get(q['id'], 'Пока нет ответа')}", actions, application_id=row.id)
                original = next((v for v in db.get(Job, row.job_id).test_questions if v['id'] == q['id']), {})
                if original.get('rubric'):
                    reply('Критерии работодателя:\n' + original['rubric'] + '\n\nОцените ответ вручную.', actions, application_id=row.id)
            else:
                reply('В этом отклике не было теста.', actions, application_id=row.id)
    else:
        return False
    return True
