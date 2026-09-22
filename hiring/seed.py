from .db import Application, Audit, Job, User
from .matching import questions


def seed(db):
    employer = User(name="Александра", company="Бюро цифровых продуктов", role="employer", demo=True)
    db.add(employer)
    db.flush()
    requirements = [{"id": f"r{i}", "skill": skill, "label": label, "type": kind, "source": "Синтетическая демонстрационная вакансия"} for i, (skill, label, kind) in enumerate([("python", "Python", "must"), ("postgresql", "PostgreSQL", "must"), ("git", "Git", "must"), ("docker", "Docker", "nice")])]
    job = Job(owner_id=employer.id, title="Junior Python-разработчик", company=employer.company, description="Ищем разработчика внутренних сервисов. Обязательно: Python, PostgreSQL и Git. Будет плюсом Docker. Важно уметь объяснить решения из своих проектов.", terms="Удалённо · Полная занятость · Условия обсуждаются", requirements=requirements)
    db.add(job)
    db.flush()
    examples = [
        ("Анна Смирнова", "Разработала на Python сервис учёта заказов. PostgreSQL использовала для хранения заказов и оптимизировала SQL-запросы. Работала с Git: ветки, pull request и code review. Собрала Docker-образ для сервиса.", {}),
        ("Михаил Волков", "Написал на Python Telegram-бота для учебного проекта. В команде использовал Git и SQL для отчётов. Сейчас ищу первую работу разработчиком и хочу развивать backend-навыки.", {"r1": "В проекте использовал PostgreSQL: создавал таблицы, писал JOIN и добавлял индексы. Docker пока не использовал.", "r3": "Нет опыта с Docker, пока запускал проект локально."}),
        ("Дарья Орлова", "Учусь на разработчика. Создала на Python приложение для заметок. Вела историю изменений через Git. Участвовала в командном хакатоне и занималась обработкой входных данных.", {}),
    ]
    for name, resume, answers in examples:
        candidate = User(name=name, role="candidate", demo=True)
        db.add(candidate)
        db.flush()
        pending = questions(resume, requirements)
        row = Application(job_id=job.id, user_id=candidate.id, resume=resume, questions=pending, answers=answers, status="ready" if all(answers.get(q["id"]) for q in pending) else "clarifying")
        db.add(row)
        db.flush()
        db.add(Audit(application_id=row.id, action="applied"))
        if answers:
            db.add(Audit(application_id=row.id, action="answered"))
    db.commit()
    return employer
