from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


class HhVacancyError(Exception):
    pass


@dataclass(frozen=True)
class HhVacancy:
    vacancy_id: str
    title: str | None
    raw_text: str
    alternate_url: str | None = None


def is_hh_vacancy_url(source_url: str | None) -> bool:
    if not source_url:
        return False

    parsed = urlparse(source_url)
    host = (parsed.netloc or "").lower()
    return (host == "hh.ru" or host.endswith(".hh.ru")) and bool(_extract_hh_vacancy_id(source_url))


def _extract_hh_vacancy_id(source_url: str) -> str | None:
    match = re.search(r"/vacancy/(\d+)", source_url)
    return match.group(1) if match else None


def _clean_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return soup.get_text(separator=" ").strip()


def _format_salary(salary: dict | None) -> str:
    if not salary:
        return ""

    parts = []
    if salary.get("from") is not None:
        parts.append(f"от {salary['from']}")
    if salary.get("to") is not None:
        parts.append(f"до {salary['to']}")
    if salary.get("currency"):
        parts.append(str(salary["currency"]))
    if salary.get("gross") is not None:
        parts.append("до вычета налогов" if salary["gross"] else "на руки")
    return " ".join(parts)


def _line(label: str, value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if item)
    value = str(value).strip()
    return f"{label}: {value}" if value else None


def _build_raw_text(data: dict, *, vacancy_id: str, source_url: str) -> str:
    key_skills = [skill.get("name") for skill in data.get("key_skills", []) if skill.get("name")]
    professional_roles = [role.get("name") for role in data.get("professional_roles", []) if role.get("name")]
    description = _clean_html(data.get("description"))

    lines = [
        _line("Вакансия", data.get("name")),
        _line("Компания", (data.get("employer") or {}).get("name")),
        _line("Регион", (data.get("area") or {}).get("name")),
        _line("Опыт", (data.get("experience") or {}).get("name")),
        _line("Занятость", (data.get("employment") or {}).get("name")),
        _line("График", (data.get("schedule") or {}).get("name")),
        _line("Зарплата", _format_salary(data.get("salary"))),
        _line("Профессиональные роли", professional_roles),
        _line("Ключевые навыки", key_skills),
        _line("Источник", data.get("alternate_url") or source_url),
        _line("HH vacancy", vacancy_id),
    ]

    result = "\n".join(line for line in lines if line)
    if description:
        result = f"{result}\n\nОписание вакансии:\n{description}"
    return result.strip()


def _get_hh_vacancy_response(vacancy_id: str, *, headers: dict[str, str]) -> requests.Response:
    session = requests.Session()
    # The desktop/dev environment can expose a local proxy that is not available
    # to the detached uvicorn process. HH API should be called directly.
    session.trust_env = False
    return session.get(
        f"https://api.hh.ru/vacancies/{vacancy_id}",
        headers=headers,
        timeout=15,
    )


def extract_hh_vacancy_from_url(source_url: str, *, contact_email: str | None = None) -> HhVacancy:
    vacancy_id = _extract_hh_vacancy_id(source_url)
    if not vacancy_id:
        raise HhVacancyError("в ссылке не найден номер вакансии")

    user_agent_email = contact_email or "contact@example.com"
    headers = {"User-Agent": f"RezumIT/1.0 ({user_agent_email})"}

    try:
        response = _get_hh_vacancy_response(vacancy_id, headers=headers)
    except requests.RequestException as exc:
        raise HhVacancyError("HH API временно недоступен") from exc

    if response.status_code == 404:
        raise HhVacancyError("вакансия не найдена или уже скрыта")
    if response.status_code != 200:
        raise HhVacancyError(f"HH API вернул статус {response.status_code}")

    data = response.json()
    raw_text = _build_raw_text(data, vacancy_id=vacancy_id, source_url=source_url)
    if len(raw_text) < 120:
        raise HhVacancyError("HH API вернул слишком мало текста")

    return HhVacancy(
        vacancy_id=vacancy_id,
        title=data.get("name"),
        raw_text=raw_text,
        alternate_url=data.get("alternate_url"),
    )
