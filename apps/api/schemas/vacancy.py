from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from core.utils import normalize_optional_text, normalize_optional_url


def _is_hh_url(value: str | None) -> bool:
    if not value:
        return False
    host = (urlparse(value).netloc or "").lower()
    return host == "hh.ru" or host.endswith(".hh.ru")


class VacancyFormData(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    source_url: str | None = None
    raw_text: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return normalize_optional_text(value)

    @field_validator("source_url", mode="before")
    @classmethod
    def validate_source_url(cls, value):
        return normalize_optional_url(value)

    @field_validator("raw_text", mode="before")
    @classmethod
    def normalize_raw_text(cls, value):
        return normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_source_or_raw_text(self):
        if not self.source_url and not self.raw_text:
            raise ValueError("Вставьте текст вакансии или ссылку на вакансию с hh.ru")
        if self.raw_text and not self.title:
            raise ValueError("Если вы вставляете текст вакансии вручную, укажите её название.")
        if self.source_url and not self.raw_text and not _is_hh_url(self.source_url):
            raise ValueError("Для ссылок не с hh.ru вставьте текст вакансии в поле ниже.")
        return self
