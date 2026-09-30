"""Bounded downloads of MAX file attachments. No external link imports or LLM calls."""
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
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


def allowed_download(host, provider):
    return provider == 'max' and host == 'fd.oneme.ru'


def max_attachment_url(value):
    parts = parsed_url(value)
    if parts.hostname != 'fd.oneme.ru' or parts.fragment:
        raise SourceError('Ссылка на вложение MAX недоступна для импорта.')
    return value


def validate_max_pdf(filename, size, url):
    if (not isinstance(filename, str) or len(filename) > 255
            or not filename.lower().endswith('.pdf') or any(ord(char) < 32 for char in filename)):
        raise SourceError('Пришлите один PDF-файл. Другие форматы пока не поддерживаются.')
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_BYTES:
        raise SourceError('PDF должен быть не больше 5 МБ.')
    return max_attachment_url(url)


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
            headers = {'User-Agent': 'RezumitHiring/1.3', 'Accept-Encoding': 'identity'}
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

def import_max_attachment(url, size, reader=None):
    max_attachment_url(url)
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_BYTES:
        raise SourceError('PDF должен быть не больше 5 МБ.')
    if not _slots.acquire(blocking=False):
        raise SourceError('Сейчас обрабатываются другие файлы. Попробуйте через минуту или вставьте текст.')
    try:
        reader = reader or PublicReader()
        raw = reader.get(url, 'max')
        if len(raw) != size:
            raise SourceError('Размер PDF не совпал с данными MAX. Отправьте файл ещё раз.')
        if not raw.startswith(b'%PDF'):
            raise SourceError('Вложение не удалось прочитать как PDF. Проверьте файл и отправьте его ещё раз.')
        try:
            text = extract_pdf(raw)
        except ValueError as exc:
            raise SourceError('Не удалось прочитать PDF. Нужен текстовый PDF до 5 МБ и 10 страниц; сканы не поддерживаются.') from exc
        return SourceResult(text, 'max',
                            'Проверьте распознанный текст. Оригинал PDF получит только работодатель выбранной вакансии после вашего подтверждения.', raw)
    finally:
        _slots.release()
