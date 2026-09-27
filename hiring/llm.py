"""Configuration and bounded transport for OpenAI-compatible model APIs."""
import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

CLOUDRU_URL = 'https://foundation-models.api.cloud.ru/v1'
CLOUDRU_MODEL = 'ai-sage/GigaChat3-10B-A1.8B'
PROVIDER_URLS = {'cloudru': CLOUDRU_URL, 'deepseek': 'https://api.deepseek.com/v1'}
MAX_RESPONSE_BYTES = 65536


@dataclass(frozen=True)
class LLMSettings:
    provider: str = 'disabled'
    base_url: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)
    allowed_hosts: tuple[str, ...] = ()
    max_tokens: int = 1000
    timeout_seconds: int = 12
    owner_daily_limit: int = 3
    daily_limit: int = 10
    total_limit: int = 50

    @classmethod
    def from_env(cls):
        provider = os.getenv('HIRING_LLM_PROVIDER')
        if provider is None:
            provider = 'cloudru' if os.getenv('HIRING_GIGACHAT_ENABLED', '').lower() == 'true' else 'disabled'
        return cls(
            provider=provider,
            base_url=(os.getenv('HIRING_LLM_BASE_URL') or PROVIDER_URLS.get(provider, '')).rstrip('/'),
            model=os.getenv('HIRING_LLM_MODEL') or (CLOUDRU_MODEL if provider == 'cloudru' else ''),
            # Cloud.ru credentials are never reused for another provider.
            api_key=os.getenv('CLOUDRU_API_KEY', '') if provider == 'cloudru' else os.getenv('LLM_API_KEY', ''),
            allowed_hosts=tuple(h.strip().lower() for h in os.getenv('HIRING_LLM_ALLOWED_HOSTS', '').split(',') if h.strip()),
            max_tokens=int(os.getenv('HIRING_LLM_MAX_TOKENS', '1000')),
            timeout_seconds=int(os.getenv('HIRING_LLM_TIMEOUT_SECONDS', '12')),
            owner_daily_limit=int(os.getenv('HIRING_LLM_OWNER_DAILY_LIMIT', '3')),
            daily_limit=int(os.getenv('HIRING_LLM_DAILY_LIMIT', '10')),
            total_limit=int(os.getenv('HIRING_LLM_TOTAL_LIMIT', '50')),
        )

    @property
    def ready(self):
        return self.provider != 'disabled' and bool(self.api_key)

    def validate(self):
        if self.provider not in ('disabled', 'cloudru', 'deepseek', 'openai_compatible'):
            raise ValueError('HIRING_LLM_PROVIDER: disabled, cloudru, deepseek or openai_compatible')
        if not (128 <= self.max_tokens <= 2500 and 2 <= self.timeout_seconds <= 30):
            raise ValueError('LLM token limit must be 128-2500; timeout must be 2-30 seconds')
        if not (1 <= self.owner_daily_limit <= self.daily_limit <= self.total_limit <= 10000):
            raise ValueError('LLM limits must satisfy 1 <= owner daily <= daily <= total <= 10000')
        if self.provider == 'disabled':
            return
        if not re.fullmatch(r'[A-Za-z0-9_./:-]{1,160}', self.model):
            raise ValueError('Set HIRING_LLM_MODEL to a model supported by the selected provider')
        fixed_url = PROVIDER_URLS.get(self.provider)
        if fixed_url and self.base_url != fixed_url:
            raise ValueError('Use the fixed base URL of the selected provider')
        try:
            url = urlsplit(self.base_url)
            host = url.hostname or ''
            if (url.scheme != 'https' or url.port not in (None, 443) or url.username or url.password
                    or url.query or url.fragment or not re.fullmatch(r'[a-z0-9.-]+', host)
                    or '.' not in host or any(c.isspace() for c in self.base_url)
                    or host.endswith(('.local', '.localhost', '.invalid'))):
                raise ValueError()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None:
                raise ValueError()
            if not fixed_url and host not in self.allowed_hosts:
                raise ValueError()
        except ValueError:
            raise ValueError('LLM base URL must use HTTPS and an explicitly allowed host, without credentials or query') from None


class LLMError(ValueError):
    """A safe error category. Never include provider bodies or credentials."""


def complete(settings: LLMSettings, messages: list[dict]) -> str:
    settings.validate()
    if not settings.ready:
        raise LLMError('not_configured')
    payload = {'model': settings.model, 'max_tokens': settings.max_tokens,
               'temperature': 0.3, 'messages': messages}
    deadline = time.monotonic() + settings.timeout_seconds
    try:
        with httpx.Client(timeout=settings.timeout_seconds, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', settings.base_url + '/chat/completions',
                               headers={'Authorization': 'Bearer ' + settings.api_key}, json=payload) as response:
                if response.status_code != 200:
                    raise LLMError('unavailable')
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise LLMError('timeout')
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise LLMError('too_large')
        choice = json.loads(data)['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise LLMError('incomplete')
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise LLMError('invalid_response')
        return content.strip()
    except LLMError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise LLMError('invalid_response_or_network') from None
