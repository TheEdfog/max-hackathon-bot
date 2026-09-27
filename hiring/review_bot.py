"""Optional employer detail views; never crawl GitHub inside the event transaction."""
from fastapi import HTTPException
from sqlalchemy import select
from .chat_ui import page_number
from .compatibility import compatibility
from .db import Application, Job
from .github_review import request_review, review_view


def handle_review(db, user, text, reply):
    parts = text.split()
    if not parts or parts[0] not in ('/match', '/github', '/github-fetch'):
        return False
    row = db.scalar(select(Application).join(Job).where(Application.id == (parts[1] if len(parts) > 1 else ''),
                                                     Job.owner_id == user.id))
    if not row or row.status == 'withdrawn':
        reply('Отклик не найден или отозван.')
        return True
    command = parts[0]
    buttons = [('К карточке', '/view ' + row.id)]
    page = page_number(parts[2] if len(parts) > 2 else 0)
    if command == '/match':
        result = compatibility(row.resume, row.answers, db.get(Job, row.job_id).requirements)
        items = result['requirements']
        page = min(page, max(0, (len(items) - 1) // 4))
        percent = lambda value: 'нет требований' if value is None else str(value) + '%'
        text = ('Покрытие требований сведениями\nОбязательные: ' + percent(result['must_pct']) +
                '\nЖелательные: ' + percent(result['nice_pct']) + '\nВзвешенный итог: ' + percent(result['total_pct']) +
                '\n\nФормула Вадима адаптирована к тексту отклика. ' + result['notice'] + '\n')
        for item in items[page * 4:(page + 1) * 4]:
            text += f"\n{item['label']}: {item['evidence_percent']}%\n{item['followup']}\n"
        if page:
            buttons.append(('⬅️ Назад', f'/match {row.id} {page - 1}'))
        if (page + 1) * 4 < len(items):
            buttons.append(('Далее ➡️', f'/match {row.id} {page + 1}'))
        buttons.append(('Цитаты и ответы', '/evidence ' + row.id))
        reply(text + '\nМетод: ' + result['method'] + '. Отрицания и противоречия не дают баллов. Обязательные/желательные: 75/25, если есть обе группы.', buttons, application_id=row.id)
        return True
    view = review_view(db, row)
    if command == '/github-fetch':
        if page >= len(view['links']):
            reply('Ссылка не найдена в отклике.', buttons, application_id=row.id)
            return True
        try:
            view = request_review(db, row, user, view['links'][page])
        except HTTPException as exc:
            reply(str(exc.detail), buttons, application_id=row.id)
            return True
    if view['status'] == 'not_requested':
        text = 'GitHub - дополнительный обзор\n\n' + view['notice'] + '\n\n'
        if view['links']:
            for index, link in enumerate(view['links']):
                text += link + '\n'
                buttons.append((f'Прочитать GitHub {index + 1}', f'/github-fetch {row.id} {index}'))
            text += '\nПрофиль, метаданные и README до 5 репозиториев. Код, приватные проекты и внешние ссылки не читаем. Без LLM; результат хранится до 24 часов.'
        else:
            text += 'Кандидат не указал поддерживаемую GitHub-ссылку в резюме или ответах. Не ищем аккаунт по имени и не подставляем чужой профиль.'
    elif view['status'] in ('pending', 'working'):
        text = 'GitHub поставлен в очередь. Пришлю уведомление, когда обзор будет готов. Повторное открытие не создаёт новый запрос.'
        buttons.append(('Проверить готовность', '/github ' + row.id))
    elif view['status'] == 'failed':
        text = view['error'] + '\nКэш ошибки - до 15 минут. Данные отклика не изменились.'
    else:
        report = view['report']
        repos = report['repositories']
        page = min(page, len(repos))
        text = 'GitHub - публичные сведения\n' + report['notice'] + '\n\n'
        if page == 0:
            text += report['url'] + '\nОписание профиля: ' + (report['profile']['bio'] or 'не указано')
            text += f'\nРепозиториев в обзоре: {len(repos)}. LLM-запросов: 0.\nПолучено: {report["fetched_at"]}'
            text += '\n' + '\n'.join(report['warnings'])
        else:
            repo = repos[page - 1]
            unavailable = {'missing': 'README отсутствует.', 'unavailable': 'Источник временно недоступен.',
                           'too_large': 'README превышает лимит 50 КБ.', 'invalid': 'Не удалось прочитать формат README.'}
            text += (repo['url'] + '\n' + ('Форк' if repo['fork'] else 'Репозиторий') +
                     (' · архив' if repo['archived'] else '') + '\nОсновной язык файлов: ' + (repo['language'] or 'нет данных') +
                     '\nОписание автора: ' + repo['description'] + '\n\nREADME (слова автора, не проверенные факты):\n' +
                     (repo['readme'] or unavailable.get(repo['readme_status'], 'README пуст.')))
        if page:
            buttons.append(('⬅️ Назад', f'/github {row.id} {page - 1}'))
        if page < len(repos):
            buttons.append(('Далее ➡️', f'/github {row.id} {page + 1}'))
    reply(text, buttons, application_id=row.id)
    return True
