"""Bounded, anonymous reads of explicitly supported public sources. No LLM calls."""
import ipaddress
import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
import httpx
from .pdf_extract import MAX_BYTES, extract_pdf

_slots = threading.BoundedSemaphore(2)


class SourceError(ValueError):
    pass


@dataclass
class SourceResult:
    text: str
    provider: str
    warning: str
    pdf: bytes | None = None


def parsed_url(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443) or '\\' in value
                or any(ord(c) < 33 for c in value) or len(value) > 2048):
            raise ValueError()
    except ValueError:
        raise SourceError('Нужна публичная HTTPS-ссылка без пароля и нестандартного порта.')
    return parts


def source_kind(value):
    p = parsed_url(value)
    if p.hostname == 'github.com' and re.fullmatch(r'/[A-Za-z0-9-]{1,39}(?:/[A-Za-z0-9_.-]{1,100})?/?', p.path) and p.path.strip('/').split('/')[-1] not in ('.', '..'):
        return 'github'
    if p.hostname in ('disk.yandex.ru', 'disk.yandex.com', 'yadi.sk') and re.fullmatch(r'/[di]/[A-Za-z0-9_-]+/?', p.path):
        return 'yandex'
    if p.hostname == 'drive.google.com' and (
            re.fullmatch(r'/file/d/[A-Za-z0-9_-]+(?:/view)?/?', p.path)
            or p.path in ('/open', '/uc') and re.fullmatch(r'[A-Za-z0-9_-]+', parse_qs(p.query).get('id', [''])[0])):
        return 'google'
    if p.hostname == 'cloud.mail.ru' and p.path.startswith('/public/'):
        return 'mail'
    raise SourceError('Поддерживаются ссылки на GitHub, файлы Яндекс Диска и Google Drive. Для остальных источников загрузите PDF или вставьте текст.')


def allowed_download(host, provider):
    if provider == 'github':
        return host == 'api.github.com'
    if provider == 'yandex':
        return host == 'cloud-api.yandex.net' or bool(re.fullmatch(r'[a-z0-9-]+\.disk\.yandex\.(?:ru|com|net)', host))
    if provider == 'google':
        return host in ('drive.google.com', 'drive.usercontent.google.com') or bool(re.fullmatch(r'doc-[a-z0-9-]+-docs\.googleusercontent\.com', host))
    return False


class PublicReader:
    def __init__(self):
        self.deadline = time.monotonic() + 25
        self.calls = 0

    def get(self, url, provider, limit=MAX_BYTES):
        for _ in range(4):
            p = parsed_url(url)
            if not allowed_download(p.hostname, provider):
                raise SourceError('Источник перенаправил запрос на неподдерживаемый адрес.')
            # Fixed vendor host allowlist is the primary boundary; reject private DNS answers too.
            addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise SourceError('Адрес источника недоступен для публичного импорта.')
            self.calls += 1
            if self.calls > 8 or time.monotonic() >= self.deadline:
                raise SourceError('Источник отвечает слишком долго. Попробуйте позже или вставьте текст.')
            headers = {'User-Agent': 'RezumitHiring/1.3 public-import', 'Accept-Encoding': 'identity'}
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get('location', ''))
                        continue
                    if response.status_code in (403, 429):
                        raise SourceError('Источник ограничил доступ или частоту запросов. Повторите позже; вход и ограничения мы не обходим.')
                    if response.status_code != 200:
                        raise SourceError('Публичный файл не найден или недоступен для скачивания.')
                    size = response.headers.get('content-length', '')
                    if size.isdigit() and int(size) > limit:
                        raise SourceError('Файл слишком большой. Лимит PDF - 5 МБ.')
                    output = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        output.extend(chunk)
                        if len(output) > limit or time.monotonic() > self.deadline:
                            raise SourceError('Превышен размер или время скачивания файла.')
                    return bytes(output)
        raise SourceError('Слишком много перенаправлений.')

    def json(self, url, provider):
        return json.loads(self.get(url, provider, 512 * 1024))


def github_import(url, reader):
    parts = parsed_url(url).path.strip('/').split('/')
    owner = parts[0]
    root = 'https://api.github.com'
    if len(parts) == 2:
        repo = reader.json(f'{root}/repos/{owner}/{parts[1]}', 'github')
        repos = [repo]
    else:
        # Five bounded items, no personal profile fields or email/contact scraping.
        repos = reader.json(f'{root}/users/{owner}/repos?sort=updated&per_page=5&type=owner', 'github')
    if not isinstance(repos, list):
        raise SourceError('Не удалось прочитать публичные репозитории GitHub.')
    lines = ['Публичное портфолио GitHub: ' + url,
             'Описание репозиториев не подтверждает личный вклад или владение навыками.']
    for repo in repos[:5]:
        if not isinstance(repo, dict) or repo.get('private') is not False:
            continue
        name = repo.get('full_name', '')
        if not re.fullmatch(r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}', name):
            continue
        label = 'форк' if repo.get('fork') else 'репозиторий'
        lines.append(f'\n{label}: https://github.com/{name}')
        languages = reader.json(f'{root}/repos/{name}/languages', 'github')
        if isinstance(languages, dict):
            lines.append('Языки файлов по GitHub API: ' + ', '.join(str(k)[:60] for k in list(languages)[:10]))
        description = repo.get('description')
        if isinstance(description, str):
            lines.append('Описание автора: ' + description[:600])
    if len(lines) == 2:
        raise SourceError('Нет доступных публичных репозиториев для импорта.')
    return SourceResult('\n'.join(lines)[:12000], 'github',
                        'Проверьте текст и укажите свой вклад. Языки репозитория не доказывают опыт кандидата. Прочитано не более 5 репозиториев.')


def _import_source(url, reader=None):
    provider = source_kind(url)
    if provider == 'mail':
        raise SourceError('Облако Mail.ru может требовать вход или подписку для скачивания. Автоимпорт пока не поддерживается: скачайте PDF самостоятельно и загрузите его через API либо вставьте текст в чат.')
    reader = reader or PublicReader()
    try:
        if provider == 'github':
            return github_import(url, reader)
        if provider == 'yandex':
            result = reader.json('https://cloud-api.yandex.net/v1/disk/public/resources/download?' +
                                 urlencode({'public_key': url}), provider)
            download = result.get('href', '') if isinstance(result, dict) else ''
        else:
            p = parsed_url(url)
            match = re.search(r'/file/d/([A-Za-z0-9_-]+)', p.path)
            file_id = match[1] if match else parse_qs(p.query)['id'][0]
            download = 'https://drive.usercontent.google.com/download?' + urlencode({'id': file_id, 'export': 'download'})
        raw = reader.get(download, provider)
        if not raw.startswith(b'%PDF'):
            raise SourceError('Ссылка не отдала PDF. Проверьте доступ «всем по ссылке». Страницы входа, папки и подтверждения скачивания не обходим.')
        return SourceResult(extract_pdf(raw), provider,
                            'Проверьте распознанный текст. Оригинал PDF получит только работодатель выбранной вакансии после вашего подтверждения.', raw)
    except SourceError:
        raise
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
        raise SourceError('Не удалось прочитать источник. Попробуйте позже или вставьте текст; сканы и защищённые PDF не поддерживаются.')


def import_source(url, reader=None):
    if not _slots.acquire(blocking=False):
        raise SourceError('Сейчас обрабатываются другие файлы. Попробуйте через минуту или вставьте текст.')
    try:
        return _import_source(url, reader)
    finally:
        _slots.release()
