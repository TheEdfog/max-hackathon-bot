import os
import secrets
from urllib.parse import urlparse
from dataclasses import dataclass, field


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: os.getenv("HIRING_DATABASE_URL", "sqlite:///data/hiring.db"))
    secret: str = field(default_factory=lambda: os.getenv("HIRING_SECRET", ""))
    production: bool = field(default_factory=lambda: os.getenv("HIRING_ENV", "development") == "production")
    demo: bool = field(default_factory=lambda: os.getenv("HIRING_DEMO", "true").lower() == "true")
    employer_code: str = field(default_factory=lambda: os.getenv("HIRING_EMPLOYER_CODE", ""))
    bot_token: str = field(default_factory=lambda: os.getenv("MAX_BOT_TOKEN", ""))
    bot_name: str = field(default_factory=lambda: os.getenv("MAX_BOT_NAME", ""))
    webhook_secret: str = field(default_factory=lambda: os.getenv("MAX_WEBHOOK_SECRET", ""))
    max_api_url: str = field(default_factory=lambda: os.getenv("MAX_API_URL", "https://platform-api2.max.ru"))
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/"))
    worker: bool = field(default_factory=lambda: os.getenv('HIRING_WORKER', 'true').lower() == 'true')
    # CLI-only opt-in; never enabled by the ordinary production environment file.
    sandbox: bool = False
    sandbox_users: tuple[str, ...] = ()

    def validate(self):
        if self.sandbox:
            from sqlalchemy.engine import make_url
            database = make_url(self.database_url)
            if self.production or database.get_backend_name() != 'sqlite' or not (database.database or '').endswith('.sandbox.db'):
                raise ValueError('Sandbox requires development and a separate *.sandbox.db database')
            if not self.sandbox_users or any(not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**63 for value in self.sandbox_users):
                raise ValueError('Sandbox requires an explicit MAX user allowlist')
        if self.bot_token and self.max_api_url != 'https://platform-api2.max.ru':
            raise ValueError('Refusing to send a MAX token to an unapproved API origin')
        if self.production:
            if not self.database_url.startswith('sqlite:'):
                raise ValueError('This release supports serialized SQLite deployment only')
            if self.demo:
                raise ValueError('Disable HIRING_DEMO in production')
            if len(self.secret) < 32 or not self.employer_code or not self.public_url.startswith("https://"):
                raise ValueError("Production requires HIRING_SECRET (32+ characters), HIRING_EMPLOYER_CODE and HTTPS PUBLIC_URL")
            public = urlparse(self.public_url)
            if not public.hostname or public.hostname.endswith('.invalid') or public.username or public.password or public.query or public.fragment or public.path or public.port not in (None, 443):
                raise ValueError('PUBLIC_URL must be a real HTTPS origin on port 443')
            if self.bot_token and len(self.webhook_secret) < 24:
                raise ValueError("MAX_WEBHOOK_SECRET must have at least 24 characters")
            if self.bot_token and not self.bot_name:
                raise ValueError('Production bot requires MAX_BOT_NAME for vacancy links')
        if not self.secret:
            self.secret = secrets.token_urlsafe(48)
