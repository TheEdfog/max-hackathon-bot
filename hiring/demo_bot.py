"""One-account guided rehearsal. Synthetic data, no jobs/applications/other recipients."""
from .matching import evidence, questions

REQUIREMENTS = [
    {'id': 'python', 'skill': 'python', 'label': 'Python', 'type': 'must'},
    {'id': 'docker', 'skill': 'docker', 'label': 'Docker', 'type': 'must'},
]
RESUME = 'Разработал на Python учебный каталог книг, написал тесты и документацию.'
INVITATION = 'Приглашаем обсудить учебный проект завтра в 15:00. Подтвердите интерес.'


def handle_demo(session, text, reply):
    state = dict(session.state)
    active = state.get('step') == 'guided_demo'
    if text == '/demo':
        previous = state.get('previous', {}) if active else state
        state = {'step': 'guided_demo', 'stage': 0, 'previous': previous}
    elif not active:
        return False
    elif text in ('/demo_exit', '/cancel'):
        session.state = state.get('previous', {})
        reply('Учебный сценарий закрыт. Реальные вакансии и отклики не изменялись; предыдущий шаг диалога восстановлен.', [('Меню', '/help')])
        return True
    elif text.startswith('/demo_next '):
        try:
            target = int(text.split()[1])
        except (ValueError, IndexError):
            target = -1
        if target != state['stage'] + 1 or not 1 <= target <= 7 or target == 4:
            reply('Продолжите учебный сценарий актуальной кнопкой или начните его заново.', [('Начать заново', '/demo'), ('Выйти из демо', '/demo_exit')])
            return True
        state['stage'] = target
    elif text in ('/demo_answer none', '/demo_answer skip') and state['stage'] == 3:
        state['answer'] = 'Опыта с Docker нет.' if text.endswith('none') else 'Пропускаю уточнение, сведений недостаточно.'
        state['stage'] = 4
    else:
        if text.startswith('/') and not text.startswith('/demo_'):
            session.state = state.get('previous', {})
            return False
        reply('В учебном сценарии используются только вымышленные данные. Выберите кнопку из последнего сообщения.', [('Начать заново', '/demo'), ('Выйти из демо', '/demo_exit')])
        return True
    session.state = state
    stage = state['stage']
    prefix = f'УЧЕБНЫЙ СЦЕНАРИЙ · {stage + 1}/8\nОдин аккаунт, вымышленные данные.\n\n'
    buttons = []
    if stage == 0:
        content = 'Вы - работодатель небольшой IT-команды. Нужен Junior Python-разработчик. Бот подготовил требования: Python и Docker. В реальном сценарии список можно исправить до публикации.'
        buttons = [('Подтвердить требования', '/demo_next 1')]
    elif stage == 1:
        content = 'Учебная вакансия готова. Обычно здесь работодатель отправляет ссылку кандидату. Сейчас переключаем точку зрения, не меняя вашу настоящую роль и не отправляя сообщений другим людям.'
        buttons = [('Посмотреть глазами кандидата', '/demo_next 2')]
    elif stage == 2:
        content = 'Вы - кандидат. Перед отправкой опыта бот объясняет, кто получит данные, и запрашивает согласие. В этой демонстрации реальные данные не нужны.'
        buttons = [('Продолжить на вымышленных данных', '/demo_next 3')]
    elif stage == 3:
        question = questions(RESUME, REQUIREMENTS)[0]['text']
        content = f'Учебное резюме:\n«{RESUME}»\n\nPython упомянут, Docker - нет. Реальный движок сформировал уточнение:\n{question}'
        buttons = [('Ответить: опыта нет', '/demo_answer none'), ('Пропустить уточнение', '/demo_answer skip')]
    elif stage == 4:
        assessment = evidence(RESUME, {'docker': state['answer']}, REQUIREMENTS)
        docker = assessment['requirements'][1]
        label = 'сообщил об отсутствии опыта' if docker['state'] == 'negative' else 'сведения не уточнены, требуется просмотр'
        content = f'Вы снова работодатель. Карточка:\n\nPython - указан в резюме.\nЦитата: «{RESUME}»\n\nDocker - {label}.\nОтвет: «{state["answer"]}»\n\nРейтинга людей и автоматического отказа нет. Вы можете пригласить кандидата, даже если опыта с Docker нет.'
        buttons = [('Подготовить приглашение', '/demo_next 5')]
    elif stage == 5:
        content = f'Учебное приглашение:\n«{INVITATION}»\n\nВ рабочем сценарии его текст задаёт работодатель. Здесь показываем результат отправки, никому другому сообщение не уйдёт.'
        buttons = [('Посмотреть уведомление кандидата', '/demo_next 6')]
    elif stage == 6:
        content = f'Вы - кандидат. Работодатель пригласил на знакомство:\n«{INVITATION}»'
        buttons = [('Подтвердить интерес (учебный)', '/demo_next 7')]
    else:
        content = 'Учебный результат: интерес подтверждён, работодатель видит итог.\n\nВ рабочем сценарии сообщения доставляются двум разным аккаунтам через MAX, а отклик сохраняется в базе. В этой демонстрации бизнес-записи не создавались.\n\nТеперь можно перейти к своей вакансии. Для полного живого прогона потребуется второй аккаунт.'
        buttons = [('Повторить учебный сценарий', '/demo')]
    reply(prefix + content, buttons + [('Выйти из демо', '/demo_exit')], bind=True)
    return True
