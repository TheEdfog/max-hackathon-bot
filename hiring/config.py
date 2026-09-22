import os
import secrets
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
    worker: bool = True

    def validate(self):
        if self.production:
            if len(self.secret) < 32 or not self.employer_code or not self.public_url.startswith("https://"):
                raise ValueError("Production requires HIRING_SECRET (32+ characters), HIRING_EMPLOYER_CODE and HTTPS PUBLIC_URL")
            if self.bot_token and len(self.webhook_secret) < 24:
                raise ValueError("MAX_WEBHOOK_SECRET must have at least 24 characters")
        if not self.secret:
            self.secret = secrets.token_urlsafe(48)
