from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from apps.api.db.database import get_db
from apps.api.repositories.cover_letter_repository import (
    get_cover_letter_by_id_for_user,
    get_cover_letter_for_pair,
)
from apps.api.repositories.resume_repository import (
    get_generated_resume_by_id_for_user,
    list_generated_resumes_for_pair,
)
from apps.web.dependencies import get_current_user
from apps.web.services.cover_letter_service import (
    CoverLetterServiceError,
    generate_and_store_cover_letter,
    revise_and_store_cover_letter,
)
from apps.web.services.match_service import build_low_match_warning, build_match_result
from apps.web.services.pdf_service import (
    PdfServiceError,
    compile_cover_letter_pdf,
    compile_pdf,
    save_cover_letter_latex_file,
    save_latex_file,
)
from apps.web.services.profile_service import (
    ProfileError,
    add_soft_skills_to_profile,
    get_user_profile,
    get_user_single_profile_or_raise,
)
from apps.web.services.recommendation_service import build_recommendations
from apps.web.services.resume_service import (
    ResumeServiceError,
    generate_and_store_resume,
    revise_and_store_resume,
)
from apps.web.services.vacancy_service import (
    VacancyError,
    activate_vacancy_for_user,
    extract_and_save_requirements,
    get_user_vacancy_or_raise,
    list_user_vacancies,
    load_vacancy_raw_text,
    refresh_vacancy_requirement_policy,
)
from core.config import settings
from core.utils import display_skill_name, is_soft_skill, normalize_skill

router = APIRouter(prefix="/result", tags=["result"])


def _message_redirect(url: str, message: str, message_type: str = "info") -> RedirectResponse:
    separator = "&" if "?" in url else "?"
    return RedirectResponse(
        url=f"{url}{separator}message={quote(message)}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )


def _build_context(
    request: Request,
    *,
    current_user,
    profile=None,
    vacancies=None,
    vacancy=None,
    match_result=None,
    recommendations=None,
    resumes=None,
    selected_resume=None,
    cover_letter=None,
    soft_skill_requirements=None,
    analysis_error: str | None = None,
):
    return {
        "request": request,
        "title": "Анализ и резюме",
        "current_user": current_user,
        "profile": profile,
        "vacancies": vacancies or [],
        "vacancy": vacancy,
        "match_result": match_result,
        "low_match_warning": build_low_match_warning(match_result),
        "recommendations": recommendations,
        "resumes": resumes or [],
        "selected_resume": selected_resume,
        "cover_letter": cover_letter,
        "soft_skill_requirements": soft_skill_requirements or [],
        "analysis_error": analysis_error,
        "flash_message": request.query_params.get("message"),
        "flash_type": request.query_params.get("message_type", "info"),
    }


def _clean_instruction(instruction: str | None) -> str:
    return (instruction or "").strip()


def _existing_pdf_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None

    path = Path(path_value)
    if not path.is_absolute():
        path = settings.project_root / path

    if path.suffix.lower() != ".pdf" or not path.is_file():
        return None

    try:
        path.resolve().relative_to(settings.generated_path.resolve())
    except ValueError:
        return None

    return path


def _prepare_analysis(
    db: Session,
    *,
    user_id: int,
    vacancy_id: int,
    use_ai_actions: bool = True,
):
    profile = get_user_single_profile_or_raise(db, user_id=user_id)
    vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
    vacancy.raw_text = load_vacancy_raw_text(vacancy)

    if not vacancy.requirements:
        extract_and_save_requirements(db, user_id=user_id, vacancy_id=vacancy.id)
        vacancy = get_user_vacancy_or_raise(db, user_id=user_id, vacancy_id=vacancy_id)
        vacancy.raw_text = load_vacancy_raw_text(vacancy)
    else:
        vacancy = refresh_vacancy_requirement_policy(db, user_id=user_id, vacancy_id=vacancy.id)
        vacancy.raw_text = load_vacancy_raw_text(vacancy)

    match_result = build_match_result(profile, vacancy)
    recommendations = build_recommendations(profile, vacancy, match_result, use_ai_actions=use_ai_actions)
    activate_vacancy_for_user(db, user_id=user_id, vacancy_id=vacancy.id)
    resumes = list_generated_resumes_for_pair(
        db,
        user_id=user_id,
        profile_id=profile.id,
        vacancy_id=vacancy.id,
    )
    cover_letter = get_cover_letter_for_pair(
        db,
        user_id=user_id,
        profile_id=profile.id,
        vacancy_id=vacancy.id,
    )
    return profile, vacancy, match_result, recommendations, resumes, cover_letter


def _soft_skill_requirements_for_profile(profile, match_result: dict) -> list[dict[str, Any]]:
    profile_skills = {normalize_skill(skill.skill_norm) for skill in getattr(profile, "skills", [])}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in match_result.get("requirements", []):
        skill_norm = normalize_skill(str(item.get("skill_norm") or ""))
        if not skill_norm or skill_norm in seen:
            continue
        if item.get("category") != "soft_skill" and not is_soft_skill(skill_norm):
            continue
        label = display_skill_name(str(item.get("display_name") or skill_norm))
        result.append(
            {
                "value": skill_norm,
                "label": label,
                "type": item.get("type"),
                "status": item.get("status"),
                "already_in_profile": skill_norm in profile_skills,
                "source_text": item.get("source_text"),
            }
        )
        seen.add(skill_norm)
    return result


def _prepare_result_page_data(
    db: Session,
    user_id: int,
    vacancy_id: int,
    resume_id: int | None,
):
    profile, vacancy, match_result, recommendations, resumes, cover_letter = _prepare_analysis(
        db,
        user_id=user_id,
        vacancy_id=vacancy_id,
    )
    selected_resume = None
    if resume_id is not None:
        selected_resume = get_generated_resume_by_id_for_user(db, user_id=user_id, resume_id=resume_id)
    if selected_resume is None and resumes:
        selected_resume = resumes[0]
    soft_skill_requirements = _soft_skill_requirements_for_profile(profile, match_result)
    return profile, vacancy, match_result, recommendations, resumes, selected_resume, cover_letter, soft_skill_requirements


def _generate_resume_for_vacancy(
    db: Session,
    user_id: int,
    vacancy_id: int,
    metrics_mode: str,
):
    profile, vacancy, match_result, recommendations, _, _ = _prepare_analysis(
        db,
        user_id=user_id,
        vacancy_id=vacancy_id,
        use_ai_actions=False,
    )
    resume = generate_and_store_resume(
        db,
        user_id=user_id,
        profile=profile,
        vacancy=vacancy,
        match_result=match_result,
        recommendations=recommendations,
        metrics_mode=metrics_mode,
    )
    return resume, match_result


def _generate_cover_letter_for_vacancy(
    db: Session,
    user_id: int,
    vacancy_id: int,
):
    profile, vacancy, match_result, recommendations, resumes, _ = _prepare_analysis(
        db,
        user_id=user_id,
        vacancy_id=vacancy_id,
        use_ai_actions=False,
    )
    letter = generate_and_store_cover_letter(
        db,
        user_id=user_id,
        profile=profile,
        vacancy=vacancy,
        match_result=match_result,
        recommendations=recommendations,
    )
    selected_resume = resumes[0] if resumes else None
    return letter, selected_resume, match_result


@router.get("", response_class=HTMLResponse, name="result_index")
async def result_index(
    request: Request,
    vacancy_id: int | None = None,
    resume_id: int | None = None,
    run: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vacancies = list_user_vacancies(db, current_user.id)
    for vacancy in vacancies:
        vacancy.raw_text = load_vacancy_raw_text(vacancy)

    profile = get_user_profile(db, current_user.id)

    if vacancy_id is None:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="result/index.html",
            context=_build_context(
                request,
                current_user=current_user,
                profile=profile,
                vacancies=vacancies,
            ),
        )

    if not run and resume_id is None:
        try:
            selected_vacancy = get_user_vacancy_or_raise(db, user_id=current_user.id, vacancy_id=vacancy_id)
            selected_vacancy.raw_text = load_vacancy_raw_text(selected_vacancy)
        except VacancyError as exc:
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="result/index.html",
                context=_build_context(
                    request,
                    current_user=current_user,
                    profile=profile,
                    vacancies=vacancies,
                    analysis_error=str(exc),
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return request.app.state.templates.TemplateResponse(
            request=request,
            name="result/index.html",
            context=_build_context(
                request,
                current_user=current_user,
                profile=profile,
                vacancies=vacancies,
                vacancy=selected_vacancy,
            ),
        )

    try:
        (
            profile,
            vacancy,
            match_result,
            recommendations,
            resumes,
            selected_resume,
            cover_letter,
            soft_skill_requirements,
        ) = await run_in_threadpool(
            _prepare_result_page_data,
            db,
            current_user.id,
            vacancy_id,
            resume_id,
        )
    except (ProfileError, VacancyError) as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="result/index.html",
            context=_build_context(
                request,
                current_user=current_user,
                profile=profile,
                vacancies=vacancies,
                analysis_error=str(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="result/index.html",
        context=_build_context(
            request,
            current_user=current_user,
            profile=profile,
            vacancies=vacancies,
            vacancy=vacancy,
            match_result=match_result,
            recommendations=recommendations,
            resumes=resumes,
            selected_resume=selected_resume,
            cover_letter=cover_letter,
            soft_skill_requirements=soft_skill_requirements,
        ),
    )


@router.get("/{vacancy_id}", name="result_for_vacancy")
async def result_for_vacancy(vacancy_id: int):
    return RedirectResponse(url=f"/result?vacancy_id={vacancy_id}", status_code=status.HTTP_302_FOUND)


@router.post("/{vacancy_id}/soft-skills", name="result_add_soft_skills")
async def result_add_soft_skills(
    vacancy_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        profile, _, match_result, _, _, _ = await run_in_threadpool(
            _prepare_analysis,
            db,
            user_id=current_user.id,
            vacancy_id=vacancy_id,
            use_ai_actions=False,
        )
    except (ProfileError, VacancyError) as exc:
        return _message_redirect(f"/result?vacancy_id={vacancy_id}&run=1", str(exc), "danger")

    form = await request.form()
    selected_values = {normalize_skill(str(value)) for value in form.getlist("soft_skill") if str(value).strip()}
    available = _soft_skill_requirements_for_profile(profile, match_result)
    selected = [(item["value"], item["label"]) for item in available if item["value"] in selected_values and not item["already_in_profile"]]

    if not selected:
        return _message_redirect(
            f"/result?vacancy_id={vacancy_id}&run=1",
            "Выберите хотя бы одно новое качество из вакансии.",
            "warning",
        )

    await run_in_threadpool(add_soft_skills_to_profile, db, user_id=current_user.id, profile_id=profile.id, soft_skills=selected)
    return _message_redirect(
        f"/result?vacancy_id={vacancy_id}&run=1",
        "Выбранные soft skills добавлены в профиль. Match и рекомендации пересчитаны.",
        "success",
    )


@router.post("/{vacancy_id}/generate", name="result_generate_resume")
async def result_generate_resume(
    vacancy_id: int,
    metrics_mode: str = Form("strict"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        resume, match_result = await run_in_threadpool(
            _generate_resume_for_vacancy,
            db,
            current_user.id,
            vacancy_id,
            metrics_mode=metrics_mode,
        )
    except (ProfileError, VacancyError, ResumeServiceError) as exc:
        return _message_redirect(f"/result?vacancy_id={vacancy_id}&run=1", str(exc), "danger")

    resume_notes = resume.resume_json.get("ats_notes", {}) if isinstance(resume.resume_json, dict) else {}
    llm_status = resume_notes.get("llm_status") if isinstance(resume_notes, dict) else None
    low_match_warning = build_low_match_warning(match_result)
    if llm_status in {"deepseek", "deepseek_revision"}:
        message = "Резюме сгенерировано РезюмИТ"
        message_type = "success"
    else:
        message = "Резюме собрано в резервном режиме. Попробуйте повторить генерацию позже, если нужен более точный вариант."
        message_type = "warning"
    if low_match_warning and llm_status in {"deepseek", "deepseek_revision"}:
        message = f"Резюме сгенерировано, но match низкий. {low_match_warning}"
        message_type = "warning"
    elif low_match_warning:
        message = f"{message} {low_match_warning}"

    return RedirectResponse(
        url=f"/result?vacancy_id={vacancy_id}&resume_id={resume.id}&run=1&message={quote(message)}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{vacancy_id}/generate-cover-letter", name="result_generate_cover_letter")
async def result_generate_cover_letter(
    vacancy_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        letter, selected_resume, match_result = await run_in_threadpool(
            _generate_cover_letter_for_vacancy,
            db,
            current_user.id,
            vacancy_id,
        )
    except (ProfileError, VacancyError, CoverLetterServiceError) as exc:
        return _message_redirect(f"/result?vacancy_id={vacancy_id}&run=1", str(exc), "danger")

    letter_notes = letter.letter_json.get("notes", {}) if isinstance(letter.letter_json, dict) else {}
    llm_status = letter_notes.get("llm_status") if isinstance(letter_notes, dict) else None
    low_match_warning = build_low_match_warning(match_result)
    if llm_status in {"deepseek", "deepseek_revision"}:
        message = "Сопроводительное письмо подготовлено"
        message_type = "success"
    else:
        message = "Сопроводительное письмо собрано в резервном режиме. Попробуйте повторить генерацию позже, если нужен более точный вариант."
        message_type = "warning"
    if low_match_warning and llm_status in {"deepseek", "deepseek_revision"}:
        message = f"Сопроводительное письмо подготовлено, но match низкий. {low_match_warning}"
        message_type = "warning"
    elif low_match_warning:
        message = f"{message} {low_match_warning}"

    resume_query = f"&resume_id={selected_resume.id}" if selected_resume else ""
    return RedirectResponse(
        url=f"/result?vacancy_id={vacancy_id}{resume_query}&run=1&message={quote(message)}&message_type={message_type}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/resume/{resume_id}/revise", name="result_revise_resume")
async def result_revise_resume(
    resume_id: int,
    instruction: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    resume = get_generated_resume_by_id_for_user(db, user_id=current_user.id, resume_id=resume_id)
    if resume is None:
        return _message_redirect("/result", "Резюме не найдено", "danger")

    clean_instruction = _clean_instruction(instruction)
    if not clean_instruction:
        return _message_redirect(
            f"/result?vacancy_id={resume.vacancy_id}&resume_id={resume.id}&run=1",
            "Напишите, что именно нужно поменять в резюме.",
            "warning",
        )

    try:
        revised_resume = await run_in_threadpool(
            revise_and_store_resume,
            db,
            user_id=current_user.id,
            resume=resume,
            instruction=clean_instruction,
        )
    except ResumeServiceError as exc:
        return _message_redirect(f"/result?vacancy_id={resume.vacancy_id}&resume_id={resume.id}&run=1", str(exc), "danger")

    return RedirectResponse(
        url=(
            f"/result?vacancy_id={revised_resume.vacancy_id}&resume_id={revised_resume.id}&run=1"
            f"&message={quote('Резюме обновлено по вашему замечанию')}&message_type=success"
        ),
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/cover-letter/{letter_id}/revise", name="result_revise_cover_letter")
async def result_revise_cover_letter(
    letter_id: int,
    instruction: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    letter = get_cover_letter_by_id_for_user(db, user_id=current_user.id, letter_id=letter_id)
    if letter is None:
        return _message_redirect("/result", "Сопроводительное письмо не найдено", "danger")

    clean_instruction = _clean_instruction(instruction)
    if not clean_instruction:
        return _message_redirect(
            f"/result?vacancy_id={letter.vacancy_id}&run=1",
            "Напишите, что именно нужно поменять в письме.",
            "warning",
        )

    try:
        revised_letter = await run_in_threadpool(
            revise_and_store_cover_letter,
            db,
            user_id=current_user.id,
            letter=letter,
            instruction=clean_instruction,
        )
    except CoverLetterServiceError as exc:
        return _message_redirect(f"/result?vacancy_id={letter.vacancy_id}&run=1", str(exc), "danger")

    return RedirectResponse(
        url=(
            f"/result?vacancy_id={revised_letter.vacancy_id}&run=1"
            f"&message={quote('Сопроводительное письмо обновлено по вашему замечанию')}&message_type=success"
        ),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/resume/{resume_id}", response_class=HTMLResponse, name="result_resume_detail")
async def result_resume_detail(
    resume_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    resume = get_generated_resume_by_id_for_user(db, user_id=current_user.id, resume_id=resume_id)
    if resume is None:
        return _message_redirect("/result", "Резюме не найдено", "danger")

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="result/resume_detail.html",
        context={
            "request": request,
            "title": "Готовое резюме",
            "current_user": current_user,
            "resume": resume,
            "flash_message": request.query_params.get("message"),
            "flash_type": request.query_params.get("message_type", "info"),
        },
    )


@router.get("/cover-letter/{letter_id}", response_class=HTMLResponse, name="result_cover_letter_detail")
async def result_cover_letter_detail(
    letter_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    letter = get_cover_letter_by_id_for_user(db, user_id=current_user.id, letter_id=letter_id)
    if letter is None:
        return _message_redirect("/result", "Сопроводительное письмо не найдено", "danger")

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="result/cover_letter_detail.html",
        context={
            "request": request,
            "title": "Сопроводительное письмо",
            "current_user": current_user,
            "letter": letter,
            "flash_message": request.query_params.get("message"),
            "flash_type": request.query_params.get("message_type", "info"),
        },
    )


@router.get("/resume/{resume_id}/export-latex", name="result_export_latex")
async def result_export_latex(
    resume_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    resume = get_generated_resume_by_id_for_user(db, user_id=current_user.id, resume_id=resume_id)
    if resume is None:
        return _message_redirect("/result", "Резюме не найдено", "danger")

    tex_path = save_latex_file(resume)
    return FileResponse(tex_path, media_type="application/x-tex", filename=tex_path.name)


@router.get("/resume/{resume_id}/export-pdf", name="result_export_pdf")
async def result_export_pdf(
    resume_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    resume = get_generated_resume_by_id_for_user(db, user_id=current_user.id, resume_id=resume_id)
    if resume is None:
        return _message_redirect("/result", "Резюме не найдено", "danger")

    existing_pdf = _existing_pdf_path(resume.pdf_path)
    if existing_pdf is not None:
        return FileResponse(existing_pdf, media_type="application/pdf", filename=existing_pdf.name)

    try:
        pdf_path = await run_in_threadpool(compile_pdf, resume)
    except PdfServiceError as exc:
        return _message_redirect(f"/result?vacancy_id={resume.vacancy_id}&resume_id={resume.id}&run=1", str(exc), "warning")

    resume.pdf_path = str(pdf_path.relative_to(settings.project_root)).replace("\\", "/")
    db.commit()
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)


@router.get("/cover-letter/{letter_id}/export-latex", name="result_cover_letter_export_latex")
async def result_cover_letter_export_latex(
    letter_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    letter = get_cover_letter_by_id_for_user(db, user_id=current_user.id, letter_id=letter_id)
    if letter is None:
        return _message_redirect("/result", "Сопроводительное письмо не найдено", "danger")

    tex_path = save_cover_letter_latex_file(letter)
    return FileResponse(tex_path, media_type="application/x-tex", filename=tex_path.name)


@router.get("/cover-letter/{letter_id}/export-pdf", name="result_cover_letter_export_pdf")
async def result_cover_letter_export_pdf(
    letter_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    letter = get_cover_letter_by_id_for_user(db, user_id=current_user.id, letter_id=letter_id)
    if letter is None:
        return _message_redirect("/result", "Сопроводительное письмо не найдено", "danger")

    existing_pdf = _existing_pdf_path(letter.pdf_path)
    if existing_pdf is not None:
        return FileResponse(existing_pdf, media_type="application/pdf", filename=existing_pdf.name)

    try:
        pdf_path = await run_in_threadpool(compile_cover_letter_pdf, letter)
    except PdfServiceError as exc:
        return _message_redirect(f"/result?vacancy_id={letter.vacancy_id}&run=1", str(exc), "warning")

    letter.pdf_path = str(pdf_path.relative_to(settings.project_root)).replace("\\", "/")
    db.commit()
    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)
