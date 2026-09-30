"""Company onboarding and one-time MAX account linking."""
import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .db import (AssessmentTemplate, BotSession, Company, CompanyInvite, ExternalJob,
                 IntegrationEvent, IntegrationKey, Job, Outbox, User, now, uid)


def create_company(db, user, name):
    company = Company(id=uid(), name=name.strip())
    db.add(company)
    db.flush()
    user.company, user.company_id, user.company_role = company.name, company.id, 'admin'
    return company


def issue_max_code(db, admin, kind, lifetime):
    code = ('connect_' if kind == 'link' else 'join_') + secrets.token_urlsafe(24)
    expires = now() + lifetime
    db.add(CompanyInvite(code_hash=hashlib.sha256(code.encode()).hexdigest(),
                         company_id=admin.company_id, created_by=admin.id,
                         kind=kind, expires_at=expires))
    return code, expires


def company_member_ids(db, user):
    if not user.company_id:
        return [user.id]
    return list(db.scalars(select(User.id).where(
        User.company_id == user.company_id, User.role == 'employer',
        User.company_role.in_(('admin', 'recruiter')))))


def handle_max_command(db, max_id, display_name, text):
    """Claim one-time onboarding codes using the verified sender ID from MAX."""
    parts = text.split(maxsplit=1)
    command = parts[0] if parts else ''
    if command not in ('/connect', '/join'):
        return False
    kind = 'link' if command == '/connect' else 'recruiter'
    code = parts[1].strip() if len(parts) == 2 else ''
    invite = db.scalar(select(CompanyInvite).where(
        CompanyInvite.code_hash == hashlib.sha256(code.encode()).hexdigest())) if code else None
    if (not invite or invite.kind != kind or invite.claimed_at
            or invite.expires_at.replace(tzinfo=timezone.utc) <= now()):
        db.add(Outbox(max_id=max_id, body={'text': 'Код недействителен, уже использован или срок его действия истёк. Запросите новый код у администратора.'}))
        return True
    company = db.get(Company, invite.company_id)
    admin = db.get(User, invite.created_by)
    existing = db.scalar(select(User).where(User.max_id == max_id))
    if not company or not admin or admin.company_id != company.id or admin.company_role != 'admin':
        db.add(Outbox(max_id=max_id, body={'text': 'Приглашение больше не активно. Попросите администратора создать новое.'}))
        return True
    if kind == 'link':
        if existing and existing.id != admin.id:
            db.add(Outbox(max_id=max_id, body={'text': 'Этот MAX уже связан с другим аккаунтом. Для защиты откликов аккаунты автоматически не объединяются.'}))
            return True
        if admin.max_id and admin.max_id != max_id:
            db.add(Outbox(max_id=max_id, body={'text': 'Аккаунт администратора уже связан с другим MAX.'}))
            return True
        admin.max_id = max_id
        member = admin
        welcome = f'Аккаунт администратора компании «{company.name}» связан с MAX.'
    else:
        if existing:
            db.add(Outbox(max_id=max_id, body={'text': 'Этот MAX уже используется в системе. Для подключения рекрутером обратитесь к администратору; данные аккаунтов автоматически не объединяются.'}))
            return True
        member = User(max_id=max_id, name=(display_name or 'Рекрутер')[:160], role='employer',
                      company=company.name, company_id=company.id, company_role='recruiter')
        db.add(member)
        db.flush()
        welcome = f'Вы подключены как рекрутер компании «{company.name}». Вакансии и кандидаты компании доступны в этом чате.'
    invite.claimed_at, invite.claimed_by = now(), member.id
    session = db.get(BotSession, member.id)
    if not session:
        db.add(BotSession(user_id=member.id, state={}))
    db.add(Outbox(max_id=max_id, body={'text': welcome + '\n\nКоманды: /jobs - вакансии, /metrics - сводка, /help - меню.'}))
    return True


def install_routes(app, db_session, current, employer):
    class InviteView(BaseModel):
        code: str
        command: str
        expires_at: str
        notice: str

    def company_admin(user=Depends(employer)):
        if not user.company_id or user.company_role != 'admin':
            raise HTTPException(403, 'Только администратор компании может управлять участниками')
        return user

    def issue(db, admin, kind, lifetime):
        code, expires = issue_max_code(db, admin, kind, lifetime)
        db.commit()
        command = '/connect ' + code if kind == 'link' else '/join ' + code
        return {'code': code, 'command': command, 'expires_at': expires.isoformat(),
                'notice': 'Одноразовый код показывается только сейчас. Передайте его нужному пользователю MAX.'}

    @app.post('/api/company/max-link', response_model=InviteView, tags=['Company'])
    def link_code(admin=Depends(company_admin), db=Depends(db_session)):
        if admin.max_id:
            raise HTTPException(409, 'Аккаунт уже связан с MAX')
        return issue(db, admin, 'link', timedelta(minutes=10))

    @app.post('/api/company/recruiter-invitations', response_model=InviteView, tags=['Company'])
    def recruiter_invitation(admin=Depends(company_admin), db=Depends(db_session)):
        return issue(db, admin, 'recruiter', timedelta(hours=24))

    @app.get('/api/company/members', tags=['Company'])
    def members(admin=Depends(company_admin), db=Depends(db_session)):
        rows = db.scalars(select(User).where(User.company_id == admin.company_id)
                          .order_by(User.company_role, User.name))
        return [{'id': row.id, 'name': row.name, 'email': row.email,
                 'role': row.company_role, 'max_connected': bool(row.max_id),
                 'active': row.role == 'employer'} for row in rows]

    @app.delete('/api/company/members/{member_id}', status_code=204, tags=['Company'])
    def remove_member(member_id: str, admin=Depends(company_admin), db=Depends(db_session)):
        member = db.get(User, member_id)
        if (not member or member.company_id != admin.company_id or member.id == admin.id
                or member.company_role != 'recruiter' or member.role != 'employer'):
            raise HTTPException(404, 'Активный рекрутер не найден')
        # Keep company data under admin ownership and invalidate outstanding API credentials.
        db.query(Job).filter(Job.owner_id == member.id).update({'owner_id': admin.id}, synchronize_session=False)
        db.query(AssessmentTemplate).filter(AssessmentTemplate.owner_id == member.id).update(
            {'owner_id': admin.id}, synchronize_session=False)
        db.query(ExternalJob).filter(ExternalJob.owner_id == member.id).update(
            {'owner_id': admin.id}, synchronize_session=False)
        db.query(IntegrationEvent).filter(IntegrationEvent.owner_id == member.id).update(
            {'owner_id': admin.id}, synchronize_session=False)
        db.query(IntegrationKey).filter(IntegrationKey.owner_id == member.id).update(
            {'revoked': True}, synchronize_session=False)
        member.role, member.company_role, member.max_id = 'disabled', 'revoked', None
        db.commit()
