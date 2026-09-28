"""Bounded, anonymous reads of explicitly supported public sources. No LLM calls."""
import ipaddress
import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit
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
    if p.hostname in ('disk.yandex.ru', 'disk.yandex.com', 'yadi.sk') and re.fullmatch(r'/[di]/[A-Za-z0-9_-]+/?', p.path):
        return 'yandex'
    raise SourceError('Поддерживаются только публичные PDF на Яндекс Диске. Для остальных источников загрузите PDF через API или вставьте текст.')


def allowed_download(host, provider):
    if provider == 'yandex':
        return host == 'cloud-api.yandex.net' or bool(re.fullmatch(r'[a-z0-9-]+\.(?:disk\.yandex\.(?:ru|com|net)|storage\.yandex\.net)', host))
    return False


class PublicReader:
    def __init__(self):
        self.deadline = time.monotonic() + 25
        self.calls = 0

    def get(self, url, provider, limit=MAX_BYTES, optional=False):
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
                    if optional and response.status_code == 404:
                        return b'{}'
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

    def json(self, url, provider, optional=False):
        return json.loads(self.get(url, provider, 512 * 1024, optional=optional))


def _import_source(url, reader=None):
    provider = source_kind(url)
    reader = reader or PublicReader()
    try:
        if provider == 'yandex':
            result = reader.json('https://cloud-api.yandex.net/v1/disk/public/resources/download?' +
                                 urlencode({'public_key': url}), provider)
            download = result.get('href', '') if isinstance(result, dict) else ''
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
