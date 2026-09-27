"""On-demand GitHub metadata/README review. No code crawl, model calls or scoring."""
import base64
import html
import re
import httpx
from datetime import timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from .db import Application, GithubReview, Job, User, now, serialize_writes
from .sources import PublicReader, SourceError, _slots, parsed_url, source_kind

NOTICE = 'Публичные сведения GitHub, не проверка авторства или уровня. Форки и описания не доказывают личный вклад. В оценку требований этот обзор не включается.'


def candidate_links(application):
    text = application.resume + '\n' + '\n'.join(application.answers.values())
    result = []
    for raw in re.findall(r'https://github\.com/[A-Za-z0-9_.\-/]+', text):
        url = raw.rstrip('.,/')
        try:
            if source_kind(url) == 'github' and url.lower() not in [v.lower() for v in result]:
                result.append(url)
        except SourceError:
            continue
    # A profile link already includes that owner's repositories.
    profiles = {parsed_url(u).path.strip('/').lower() for u in result if parsed_url(u).path.count('/') == 1}
    return [u for u in result if '/' not in parsed_url(u).path.strip('/') or
            parsed_url(u).path.strip('/').split('/')[0].lower() not in profiles][:3]


def plain(value, limit):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'<[^>]*>', '', html.unescape(value))
    value = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', value)
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[ссылка/контакт скрыты]', value)
    return ''.join(c for c in value if c in '\n\t' or (ord(c) >= 32 and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069'))[:limit]


def fetch_overview(url, reader=None):
    if source_kind(url) != 'github':
        raise SourceError('Нужна ссылка GitHub из отклика.')
    if not _slots.acquire(blocking=False):
        raise SourceError('Сейчас обрабатываются другие источники. Повторите позже.')
    reader = reader or PublicReader()
    try:
        parts = parsed_url(url).path.strip('/').split('/')
        owner = parts[0]
        root = 'https://api.github.com'
        profile = reader.json(f'{root}/users/{owner}', 'github')
        if not isinstance(profile, dict) or str(profile.get('login', '')).lower() != owner.lower():
            raise SourceError('Профиль GitHub не найден или переименован. Уточните ссылку у кандидата.')
        repos = ([reader.json(f'{root}/repos/{owner}/{parts[1]}', 'github')] if len(parts) == 2 else
                 reader.json(f'{root}/users/{owner}/repos?sort=updated&per_page=5&type=owner', 'github'))
        if not isinstance(repos, list):
            raise SourceError('Не удалось прочитать репозитории GitHub.')
        items, warnings = [], []
        for repo in repos[:5]:
            if not isinstance(repo, dict) or repo.get('private') is not False:
                continue
            name = repo.get('full_name', '')
            if not re.fullmatch(re.escape(owner) + r'/[A-Za-z0-9_.-]{1,100}', name, re.I) or name.split('/')[-1] in ('.', '..'):
                continue
            readme, readme_status = '', 'missing'
            try:
                data = reader.json(f'{root}/repos/{name}/readme', 'github', optional=True)
                if isinstance(data, dict) and data.get('encoding') == 'base64':
                    content = data.get('content', '')
                    if not isinstance(content, str) or len(content) > 72000 or not isinstance(data.get('size'), int) or data['size'] > 50000:
                        readme_status = 'too_large'
                    else:
                        raw = base64.b64decode(re.sub(r'\s', '', content), validate=True)
                        if len(raw) > 50000:
                            readme_status = 'too_large'
                        else:
                            normalized = plain(raw.decode('utf-8', 'replace'), 2001)
                            readme, readme_status = normalized[:2000], 'excerpt' if len(normalized) > 2000 else 'read'
            except (SourceError, httpx.HTTPError):
                readme_status = 'unavailable'
                warnings.append('Часть README недоступна или достигнут лимит API. Показаны только полученные данные.')
                # Do not keep contacting a quota-limited or unavailable provider.
                items.append({'url': f'https://github.com/{name}', 'name': name, 'fork': bool(repo.get('fork')),
                              'archived': bool(repo.get('archived')), 'description': plain(repo.get('description'), 600),
                              'language': plain(repo.get('language'), 60), 'readme': '', 'readme_status': readme_status})
                break
            except (ValueError, TypeError):
                readme_status = 'invalid'
            items.append({'url': f'https://github.com/{name}', 'name': name, 'fork': bool(repo.get('fork')),
                          'archived': bool(repo.get('archived')), 'description': plain(repo.get('description'), 600),
                          'language': plain(repo.get('language'), 60), 'readme': readme, 'readme_status': readme_status})
        return {'url': url, 'profile': {'login': owner, 'bio': plain(profile.get('bio'), 500)},
                'repositories': items, 'warnings': list(dict.fromkeys(warnings)), 'notice': NOTICE,
                'llm_calls': 0, 'max_repositories': 5, 'fetched_at': now().isoformat()}
    except SourceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, httpx.HTTPError):
        raise SourceError('Не удалось прочитать публичный GitHub. Попробуйте позже.')
    finally:
        _slots.release()


def active_review(db, application_id):
    row = db.get(GithubReview, application_id)
    return row if row and row.expires_at.replace(tzinfo=timezone.utc) > now() else None


def review_view(db, application):
    if application.status == 'withdrawn':
        raise HTTPException(410, 'Отклик отозван')
    row = active_review(db, application.id)
    return {'status': row.status if row else 'not_requested', 'links': candidate_links(application),
            'report': row.report if row and row.status == 'ready' else None,
            'error': row.error if row and row.status == 'failed' else '',
            'expires_at': row.expires_at if row else None, 'notice': NOTICE}


def request_review(db, application, owner, url):
    if application.status == 'withdrawn':
        raise HTTPException(410, 'Отклик отозван')
    if url not in candidate_links(application):
        raise HTTPException(422, 'Можно прочитать только GitHub-ссылку, которую кандидат указал в отклике')
    existing = active_review(db, application.id)
    if existing:
        if existing.url != url:
            raise HTTPException(409, 'Для отклика уже выбран другой источник. Дождитесь истечения кэша.')
        return review_view(db, application)
    count = db.scalar(select(func.count()).select_from(GithubReview).where(
        GithubReview.owner_id == owner.id, GithubReview.created_at > now() - timedelta(hours=1)))
    pending = db.scalar(select(func.count()).select_from(GithubReview).where(GithubReview.status.in_(['pending', 'working'])))
    if count >= 5 or pending >= 100:
        raise HTTPException(429, 'Лимит: 5 GitHub-обзоров в час на работодателя. Попробуйте позже.')
    old = db.get(GithubReview, application.id)
    if old:
        db.delete(old)
        db.flush()
    db.add(GithubReview(application_id=application.id, owner_id=owner.id, url=url, expires_at=now() + timedelta(hours=24)))
    db.flush()
    return review_view(db, application)


def deliver_review(factory, fetch=fetch_overview):
    with factory() as db:
        serialize_writes(db)
        for old in db.scalars(select(GithubReview).where(GithubReview.expires_at <= now())):
            db.delete(old)
        row = db.scalar(select(GithubReview).where(GithubReview.expires_at > now(), or_(
            GithubReview.status == 'pending', (GithubReview.status == 'working') & (GithubReview.lease_until < now()))).limit(1))
        if not row:
            db.commit()
            return False
        application = db.get(Application, row.application_id)
        if not application or application.status == 'withdrawn':
            db.delete(row)
            db.commit()
            return True
        row.status, row.lease_until = 'working', now() + timedelta(minutes=2)
        aid, url, requested = row.application_id, row.url, row.created_at
        db.commit()
    try:
        result, error = fetch(url), ''
    except Exception:
        result, error = {}, 'GitHub недоступен или ограничил запросы. Повторный запрос возможен после истечения кэша; сам отклик доступен.'
    with factory() as db:
        serialize_writes(db)
        row, application = db.get(GithubReview, aid), db.get(Application, aid)
        if (not row or row.status != 'working' or row.created_at != requested or not application
                or application.status == 'withdrawn' or row.expires_at.replace(tzinfo=timezone.utc) <= now()):
            return True
        row.report, row.error, row.status = result, error, 'failed' if error else 'ready'
        if error:
            row.expires_at = min(row.expires_at.replace(tzinfo=timezone.utc), now() + timedelta(minutes=15))
        owner = db.get(User, row.owner_id)
        if owner and owner.max_id:
            from .chat_ui import queue_message
            queue_message(db, owner, 'GitHub-обзор ' + ('недоступен.' if error else 'готов.'),
                          [('Открыть обзор', '/github ' + aid), ('К карточке', '/view ' + aid)], application_id=aid)
        db.commit()
    return True
