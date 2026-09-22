from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import stat
from time import time_ns

from sqlalchemy.orm import Session

from apps.api.db.models import RequirementType, Vacancy, VacancyRequirement
from apps.api.repositories.vacancy_repository import (
    create_vacancy,
    delete_vacancy_for_user,
    get_vacancy_by_id_for_user,
    list_vacancies_for_user,
    replace_vacancy_requirements,
    set_active_vacancy_for_user,
    update_vacancy,
)
from apps.api.schemas.vacancy import VacancyFormData
from apps.web.services.hh_service import (
    HhVacancyError,
    extract_hh_vacancy_from_url,
    is_hh_vacancy_url,
)
from apps.web.services.vacancy_parser_service import apply_soft_skill_role_policy, extract_vacancy_requirements
from core.config import settings


class VacancyError(Exception):
    pass


AUTO_EXTRACT_FAILED_PREFIX = "Текст вакансии не удалось получить автоматически."


@dataclass(frozen=True)
class ResolvedVacancyContent:
    title: str | None
    raw_text: str


def list_user_vacancies(db: Session, user_id: int) -> list[Vacancy]:
    return list_vacancies_for_user(db, user_id)


def get_user_vacancy_or_raise(db: Session, user_id: int, vacancy_id: int) -> Vacancy:
    vacancy = get_vacancy_by_id_for_user(db, user_id=user_id, vacancy_id=vacancy_id)
    if vacancy is None:
        raise VacancyError("Вакансия не найдена")
    return vacancy


def _build_raw_file_name(*, user_id: int, source_url: str | None, raw_text: str) -> str:
    digest = sha256()
    digest.update(str(user_id).encode("utf-8"))
    digest.update((source_url or "").encode("utf-8"))
    digest.update(raw_text.encode("utf-8"))
    digest.update(str(time_ns()).encode("utf-8"))
    return f"{digest.hexdigest()}.txt"


def _write_raw_vacancy_file(*, file_name: str, raw_text: str) -> str:
    file_path = settings.vacancy_raw_path / file_name
    file_path.write_text(raw_text, encoding="utf-8")
    return str(file_path.relative_to(settings.project_root)).replace("\\", "/")


def load_vacancy_raw_text(vacancy: Vacancy) -> str:
    if vacancy.raw_data_path:
        file_path = settings.project_root / vacancy.raw_data_path
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")
    return vacancy.raw_text


def is_auto_extract_placeholder(raw_text: str | None) -> bool:
    return bool(raw_text and raw_text.startswith(AUTO_EXTRACT_FAILED_PREFIX))


def _build_auto_extract_placeholder(source_url: str) -> str:
    return (
        f"{AUTO_EXTRACT_FAILED_PREFIX}\n\n"
        f"Ссылка сохранена: {source_url}\n\n"
        "Что сделать дальше:\n"
        "1. Откройте вакансию по ссылке.\n"
        "2. Скопируйте название, обязанности, требования и условия.\n"
        "3. Вернитесь к редактированию вакансии и вставьте текст в поле «Текст вакансии».\n\n"
        "После этого РезюмИТ сможет выделить требования и посчитать совпадение с вашим профилем."
    )


def _resolve_vacancy_content(
    form_data: VacancyFormData,
    *,
    contact_email: str | None = None,
) -> ResolvedVacancyContent:
    if form_data.raw_text:
        return ResolvedVacancyContent(title=form_data.title, raw_text=form_data.raw_text)

    if not form_data.source_url:
        raise VacancyError("Вставьте текст вакансии или ссылку на вакансию с hh.ru.")

    if not is_hh_vacancy_url(form_data.source_url):
        raise VacancyError(
            "Автоматически можно прочитать только ссылку на hh.ru. "
            "Если вакансия с другого сайта, скопируйте её текст и вставьте в поле «Текст вакансии»."
        )

    try:
        hh_vacancy = extract_hh_vacancy_from_url(form_data.source_url, contact_email=contact_email)
    except HhVacancyError as exc:
        raise VacancyError(f"Не удалось получить вакансию через HH API: {exc}") from exc

    return ResolvedVacancyContent(
        title=form_data.title or hh_vacancy.title,
        raw_text=hh_vacancy.raw_text,
    )


def create_user_vacancy(
    db: Session,
    user_id: int,
    form_data: VacancyFormData,
    *,
    contact_email: str | None = None,
) -> Vacancy:
    resolved = _resolve_vacancy_content(form_data, contact_email=contact_email)
    file_name = _build_raw_file_name(
        user_id=user_id,
        source_url=form_data.source_url,
        raw_text=resolved.raw_text,
    )
    raw_data_path = _write_raw_vacancy_file(file_name=file_name, raw_text=resolved.raw_text)

    return create_vacancy(
        db=db,
        user_id=user_id,
        title=resolved.title,
        source_url=form_data.source_url,
        raw_text=resolved.raw_text,
        raw_data_path=raw_data_path,
    )


def update_user_vacancy(
    db: Session,
    user_id: int,
    vacancy_id: int,
    form_data: VacancyFormData,
    *,
    contact_email: str | None = None,
) -> Vacancy:
    vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
    resolved = _resolve_vacancy_content(form_data, contact_email=contact_email)
    file_name = _build_raw_file_name(
        user_id=user_id,
        source_url=form_data.source_url,
        raw_text=resolved.raw_text,
    )
    raw_data_path = _write_raw_vacancy_file(file_name=file_name, raw_text=resolved.raw_text)

    return update_vacancy(
        db=db,
        vacancy=vacancy,
        title=resolved.title,
        source_url=form_data.source_url,
        raw_text=resolved.raw_text,
        raw_data_path=raw_data_path,
    )


def activate_vacancy_for_user(db: Session, user_id: int, vacancy_id: int) -> None:
    vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
    set_active_vacancy_for_user(db, user_id=user_id, vacancy_id=vacancy.id)


def _requirement_type_value(requirement: VacancyRequirement) -> str:
    if isinstance(requirement.type, RequirementType):
        return requirement.type.value
    return RequirementType.NICE.value if str(requirement.type) == RequirementType.NICE.value else RequirementType.MUST.value


def _vacancy_role_context(vacancy: Vacancy, raw_text: str) -> str:
    if not vacancy.title:
        return raw_text
    return f"Вакансия: {vacancy.title}\n{raw_text}"


def refresh_vacancy_requirement_policy(db: Session, user_id: int, vacancy_id: int) -> Vacancy:
    vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
    if not vacancy.requirements:
        return vacancy

    raw_text = load_vacancy_raw_text(vacancy)
    current_requirements = [
        {
            "skill_norm": requirement.skill_norm,
            "display_name": requirement.display_name,
            "category": requirement.category,
            "type": _requirement_type_value(requirement),
            "source_text": requirement.source_text,
            "confidence": requirement.confidence,
        }
        for requirement in vacancy.requirements
    ]
    updated_requirements = apply_soft_skill_role_policy(_vacancy_role_context(vacancy, raw_text), current_requirements)

    current_signature = {
        (
            item["skill_norm"],
            item["type"],
            item.get("category") or "other",
            item.get("display_name") or "",
        )
        for item in current_requirements
    }
    updated_signature = {
        (
            item["skill_norm"],
            item["type"],
            item.get("category") or "other",
            item.get("display_name") or "",
        )
        for item in updated_requirements
    }
    if current_signature == updated_signature and len(current_requirements) == len(updated_requirements):
        return vacancy

    return replace_vacancy_requirements(db, vacancy, updated_requirements)


def _safe_unlink_project_file(relative_path: str | None) -> None:
    if not relative_path:
        return

    project_root = settings.project_root.resolve()
    file_path = (project_root / relative_path).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return

    if not file_path.is_file():
        return

    try:
        file_path.unlink(missing_ok=True)
    except PermissionError:
        try:
            file_path.chmod(file_path.stat().st_mode | stat.S_IWRITE)
            file_path.unlink(missing_ok=True)
        except OSError:
            return
    except OSError:
        return


def _delete_resume_export_files(resume_rows: list[tuple[int, str | None]]) -> None:
    generated_relative_path = settings.generated_path.relative_to(settings.project_root)
    for resume_id, pdf_path in resume_rows:
        _safe_unlink_project_file(pdf_path)
        _safe_unlink_project_file(str(generated_relative_path / f"resume_{resume_id}.pdf"))
        _safe_unlink_project_file(str(generated_relative_path / f"resume_{resume_id}.tex"))


def _delete_cover_letter_export_files(letter_rows: list[tuple[int, str | None]]) -> None:
    generated_relative_path = settings.generated_path.relative_to(settings.project_root)
    for letter_id, pdf_path in letter_rows:
        _safe_unlink_project_file(pdf_path)
        _safe_unlink_project_file(str(generated_relative_path / f"cover_letter_{letter_id}.pdf"))
        _safe_unlink_project_file(str(generated_relative_path / f"cover_letter_{letter_id}.tex"))


def delete_user_vacancy(db: Session, user_id: int, vacancy_id: int) -> None:
    get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
    delete_result = delete_vacancy_for_user(db, user_id=user_id, vacancy_id=vacancy_id)
    if delete_result is None:
        raise VacancyError("Вакансия не найдена")

    raw_data_path, resume_rows, letter_rows = delete_result
    _safe_unlink_project_file(raw_data_path)
    _delete_resume_export_files(resume_rows)
    _delete_cover_letter_export_files(letter_rows)


def extract_and_save_requirements(db: Session, user_id: int, vacancy_id: int) -> Vacancy:
    vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
    raw_text = load_vacancy_raw_text(vacancy)
    requirements = extract_vacancy_requirements(raw_text, vacancy_title=vacancy.title)
    if not requirements:
        return vacancy
    return replace_vacancy_requirements(db, vacancy, requirements)


def vacancy_to_form_initial(vacancy: Vacancy | None = None) -> dict[str, str]:
    if vacancy is None:
        return {
            "title": "",
            "source_url": "",
            "raw_text": "",
        }

    return {
        "title": vacancy.title or "",
        "source_url": vacancy.source_url or "",
        "raw_text": load_vacancy_raw_text(vacancy),
    }
