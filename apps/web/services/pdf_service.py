"""
Преобразование JSON -> LaTeX -> PDF.
"""
from __future__ import annotations

import shutil
import subprocess
import os
import sys
import re
from pathlib import Path
from typing import Any

from apps.api.db.models import GeneratedCoverLetter, GeneratedResume
from core.config import settings
from core.utils import display_skill_name


class PdfServiceError(Exception):
    pass


LATEX_REPLACEMENTS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

SERVICE_LABEL_RE = re.compile(
    r"^(?:Место работы|Проект|Образование|Курс или сертификат|Активность)\s*\d+$",
    flags=re.IGNORECASE,
)

LATEX_FONT_SETUP = r"""\IfFontExistsTF{Times New Roman}{
    \setmainfont{Times New Roman}
}{
    \setmainfont{Liberation Serif}
}
"""

MONTH_NAMES_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def _latex_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return "".join(LATEX_REPLACEMENTS.get(char, char) for char in text)


def _latex_href(target: str, label: str | None = None) -> str:
    raw_target = str(target or "").strip()
    if not raw_target:
        return ""

    raw_label = str(label or raw_target).strip()
    safe_target = raw_target.replace("{", "").replace("}", "").replace("\\", "/")
    return (
        r"\href{\detokenize{"
        + safe_target
        + r"}}{\cvlinktext{"
        + _latex_escape(raw_label)
        + r"}}"
    )


def _short_link_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"^https?://", "", text, flags=re.IGNORECASE).rstrip("/")


def _contact_segments(contact: dict[str, Any]) -> list[str]:
    segments: list[str] = []

    email = str(contact.get("email") or "").strip()
    phone = str(contact.get("phone") or "").strip()
    city = str(contact.get("city") or "").strip()
    work_format = str(contact.get("work_format") or "").strip()
    github_url = str(contact.get("github_url") or "").strip()
    linkedin_url = str(contact.get("linkedin_url") or "").strip()

    if email:
        segments.append(_latex_href(f"mailto:{email}", email))
    if phone:
        tel_value = re.sub(r"[^\d+]", "", phone)
        if tel_value:
            segments.append(_latex_href(f"tel:{tel_value}", phone))
        else:
            segments.append(_latex_escape(phone))
    if city:
        segments.append(_latex_escape(city))
    if work_format:
        segments.append(_latex_escape(work_format))
    if github_url:
        segments.append(_latex_href(github_url, _short_link_label(github_url)))
    if linkedin_url:
        segments.append(_latex_href(linkedin_url, _short_link_label(linkedin_url)))

    return segments


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text_items(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if item is None:
            continue
        if isinstance(item, dict):
            text = ", ".join(str(part) for part in item.values() if part)
        else:
            text = str(item)
        text = text.strip()
        if text and not SERVICE_LABEL_RE.fullmatch(text):
            result.append(text)
    return result


def _skill_items(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_item in _text_items(value):
        chunks = [chunk.strip() for chunk in raw_item.replace(";", ",").split(",") if chunk.strip()]
        for chunk in chunks:
            display_name = display_skill_name(chunk)
            key = display_name.lower()
            if display_name and key not in seen:
                result.append(display_name)
                seen.add(key)
    return result


def _join_non_empty(parts: list[Any], separator: str = " | ") -> str:
    return separator.join(str(part).strip() for part in parts if part and str(part).strip())


def _format_month_year(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", text)
    if not match:
        return text

    year = match.group(1)
    month = int(match.group(2))
    month_name = MONTH_NAMES_RU.get(month)
    return f"{month_name} {year}" if month_name else text


def _format_period(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""

    parts = re.split(r"\s+(?:-|—|–)\s+", text, maxsplit=1)
    if len(parts) == 2:
        return f"{_format_month_year(parts[0])} – {_format_month_year(parts[1])}"

    return _format_month_year(text)


def _onecol_block(body: str) -> str:
    if not body.strip():
        return ""
    return "\\begin{onecolentry}\n" + body.strip() + "\n\\end{onecolentry}\n"


def _highlights_block_from_escaped(items: list[str]) -> str:
    if not items:
        return ""
    body = "\n".join(rf"\item {item}" for item in items if item)
    return "\\begin{highlights}\n" + body + "\n\\end{highlights}\n"


def _items_block(items: Any) -> str:
    text_items = _text_items(items)
    if not text_items:
        return ""
    return _onecol_block(_highlights_block_from_escaped([_latex_escape(item) for item in text_items]))


def _paragraphs_block(items: Any) -> str:
    text_items = _text_items(items)
    if not text_items:
        return ""
    return _onecol_block("\n\n".join(_latex_escape(item) for item in text_items))


def _skills_block(skills: Any) -> str:
    if not isinstance(skills, dict):
        skills = {"relevant": _text_items(skills)}

    lines = []
    relevant_skills = _join_non_empty(_skill_items(skills.get("relevant")), ", ")
    other_skills = _join_non_empty(_skill_items(skills.get("other")), ", ")
    soft_skills = _join_non_empty(_skill_items(skills.get("soft")), ", ")

    if relevant_skills:
        lines.append(r"\textbf{Стек и технологии:} " + _latex_escape(relevant_skills))
    if other_skills:
        lines.append(r"\textbf{Дополнительные инструменты:} " + _latex_escape(other_skills))
    if soft_skills:
        lines.append(r"\textbf{Профессиональные качества:} " + _latex_escape(soft_skills))
    return _onecol_block(_highlights_block_from_escaped(lines))


def _experience_heading(company: Any, position: Any, period: str = "", location: Any = "") -> str:
    company_text = str(company or "").strip()
    position_text = str(position or "").strip()
    period_text = str(period or "").strip()
    location_text = str(location or "").strip()

    left_top = position_text or company_text
    left_bottom = company_text if company_text and position_text else ""

    if not any([left_top, left_bottom, period_text, location_text]):
        return ""

    def emphasize(value: str) -> str:
        if not value:
            return ""
        return (
            r"{\fontsize{13pt}{15pt}\selectfont\bfseries\itshape "
            + _latex_escape(value)
            + r"}"
        )

    top_left = emphasize(left_top)
    top_right = emphasize(period_text)
    bottom_left = emphasize(left_bottom)
    bottom_right = emphasize(location_text)

    rows = [top_left + " & " + top_right]
    if bottom_left or bottom_right:
        rows.append(bottom_left + " & " + bottom_right)

    body = (
        r"\begin{tabularx}{\linewidth}{@{}>{\raggedright\arraybackslash}X"
        r">{\raggedleft\arraybackslash}p{0.38\linewidth}@{}}"
        + r"\\[-0.02cm]".join(rows)
        + r"\end{tabularx}"
    )
    return _onecol_block(body)


def _experience_block(entries: Any) -> str:
    blocks = []
    fallback_items = []
    for entry in _as_list(entries):
        if not isinstance(entry, dict):
            if entry:
                fallback_items.append(entry)
            continue

        position = entry.get("position") or entry.get("title") or entry.get("role")
        company = entry.get("company")
        location = entry.get("location") or entry.get("city")
        period = _format_period(
            entry.get("period") or _join_non_empty([entry.get("start_date"), entry.get("end_date")], " - ")
        )
        if company or position or period or location:
            blocks.append(_experience_heading(company, position, period, location))

        points = _text_items(entry.get("tasks")) + _text_items(entry.get("achievements"))
        if not points:
            points = _text_items([entry.get("description"), entry.get("result")])
        if points:
            escaped_points = [_latex_escape(point) for point in points]
            blocks.append(_onecol_block(_highlights_block_from_escaped(escaped_points)))

    if fallback_items:
        blocks.append(_items_block(fallback_items))
    return "\n".join(block for block in blocks if block)


def _projects_block(entries: Any) -> str:
    blocks = []
    fallback_items = []
    for entry in _as_list(entries):
        if not isinstance(entry, dict):
            if entry:
                fallback_items.append(entry)
            continue

        name = entry.get("name") or entry.get("title")
        role = entry.get("role")
        header = _join_non_empty([name, role], " - ")
        if header:
            blocks.append(_onecol_block(r"\textbf{" + _latex_escape(header) + "}"))

        stack = _join_non_empty(_skill_items(entry.get("stack")), ", ")
        points = _text_items([entry.get("description"), entry.get("result")])
        if stack:
            points.append("Технологии: " + stack)
        if points:
            escaped_points = [_latex_escape(point) for point in points]
            blocks.append(_onecol_block(_highlights_block_from_escaped(escaped_points)))

    if fallback_items:
        blocks.append(_items_block(fallback_items))
    return "\n".join(block for block in blocks if block)


def _section(title: str, body: str) -> str:
    if not body.strip():
        return ""
    return f"\\cvsection{{{_latex_escape(title)}}}\n{body}\n"


def render_latex_resume(resume_json: dict) -> str:
    contact = resume_json.get("contact", {}) or {}
    if not isinstance(contact, dict):
        contact = {}

    contact_line = r" \enspace\textbar\enspace ".join(_contact_segments(contact))

    summary_body = _paragraphs_block(resume_json.get("summary"))
    skills_body = _skills_block(resume_json.get("skills"))
    experience_body = _experience_block(resume_json.get("experience"))
    projects_body = _projects_block(resume_json.get("projects"))
    full_name = contact.get("full_name", "") or "Кандидат"
    target_position = resume_json.get("target_position", "")
    contact_block = f"{{\\fontsize{{12pt}}{{14pt}}\\selectfont {contact_line}}}\n" if contact_line else ""

    return r"""\documentclass[12pt,a4paper]{article}
\usepackage[
    ignoreheadfoot,
    top=1.8cm,
    bottom=1.8cm,
    left=1.9cm,
    right=1.9cm,
    footskip=0.8cm
]{geometry}
\usepackage{fontspec}
\usepackage[russian,english]{babel}
\usepackage{tabularx}
\usepackage[
    unicode=true,
    hidelinks
]{hyperref}
\urlstyle{same}
""" + LATEX_FONT_SETUP + r"""
\pagestyle{empty}
\setcounter{secnumdepth}{0}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\setlength{\topskip}{0pt}
\setlength{\emergencystretch}{2em}
\tolerance=1800
\pretolerance=100
\newcommand{\cvlinktext}[1]{\underline{#1}}
\newcommand{\cvsection}[1]{
    \vspace{0.30cm}
    \noindent{\large\bfseries #1}
    \par\vspace{0.05cm}
    \hrule
    \vspace{0.16cm}
}
\newenvironment{highlights}{
    \begin{list}{--}{
        \setlength{\leftmargin}{0.48cm}
        \setlength{\rightmargin}{0pt}
        \setlength{\itemsep}{0pt}
        \setlength{\parsep}{0.05cm}
        \setlength{\topsep}{0.08cm}
        \setlength{\partopsep}{0pt}
    }
}{
    \end{list}
}
\newenvironment{onecolentry}{
    \begin{list}{}{
        \setlength{\leftmargin}{0pt}
        \setlength{\rightmargin}{0pt}
        \setlength{\listparindent}{0pt}
        \setlength{\itemindent}{0pt}
        \setlength{\itemsep}{0pt}
        \setlength{\topsep}{0pt}
    }
    \item[]
}{
    \end{list}
}
\begin{document}
\fontsize{12pt}{14.2pt}\selectfont
""" + f"""
\\begin{{center}}
{{\\textbf{{\\fontsize{{24pt}}{{24pt}}\\selectfont {_latex_escape(full_name)}}}}}\\\\[0.18cm]
{{\\textbf{{\\fontsize{{15pt}}{{15pt}}\\selectfont {_latex_escape(target_position)}}}}}\\\\[0.18cm]
{contact_block}
\\end{{center}}

""" + _section("Технические навыки", skills_body) + _section(
        "Опыт работы",
        experience_body,
    ) + _section(
        "Проекты",
        projects_body,
    ) + _section(
        "Образование",
        _items_block(resume_json.get("education")),
    ) + _section(
        "Курсы и сертификаты",
        _items_block(resume_json.get("certificates")),
    ) + _section(
        "О себе",
        summary_body,
    ) + _section(
        "Дополнительная информация",
        _items_block(resume_json.get("languages")),
    ) + r"""
\end{document}
"""


def render_latex_cover_letter(letter_json: dict) -> str:
    paragraphs = _text_items(letter_json.get("paragraphs"))
    body = "\n\n".join(_latex_escape(paragraph) for paragraph in paragraphs)
    closing = _latex_escape(letter_json.get("closing", ""))
    candidate_name = _latex_escape(letter_json.get("candidate_name", ""))
    subject = _latex_escape(letter_json.get("subject", "Сопроводительное письмо"))
    greeting = _latex_escape(letter_json.get("greeting", "Здравствуйте!"))

    closing_block = ""
    if closing:
        closing_block += f"\n\n{closing}"
    if candidate_name:
        closing_block += f"\n\n{candidate_name}"

    return r"""\documentclass[12pt,a4paper]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{fontspec}
\usepackage[russian,english]{babel}
""" + LATEX_FONT_SETUP + r"""
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.65em}
\setlength{\emergencystretch}{3em}
\tolerance=2200
\pretolerance=100
\sloppy
\pagenumbering{gobble}
\begin{document}
\fontsize{12pt}{14.4pt}\selectfont
""" + f"""
{{\\Large \\textbf{{{subject}}}}}

{greeting}

{body}
{closing_block}
""" + r"""
\end{document}
"""


def save_latex_file(resume: GeneratedResume) -> Path:
    settings.ensure_runtime_directories()
    file_path = settings.generated_path / f"resume_{resume.id}.tex"
    latex_source = render_latex_resume(resume.resume_json) if isinstance(resume.resume_json, dict) else resume.latex_source
    file_path.write_text(latex_source, encoding="utf-8")
    return file_path


def save_cover_letter_latex_file(letter: GeneratedCoverLetter) -> Path:
    settings.ensure_runtime_directories()
    file_path = settings.generated_path / f"cover_letter_{letter.id}.tex"
    latex_source = render_latex_cover_letter(letter.letter_json) if isinstance(letter.letter_json, dict) else letter.latex_source
    file_path.write_text(latex_source, encoding="utf-8")
    return file_path


def _resolve_latex_engine() -> Path | str:
    found = shutil.which(settings.latex_engine)
    if found:
        return found

    executable = f"{settings.latex_engine}.exe"
    candidate_roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
        Path(os.environ.get("ProgramFiles", "")) / "MiKTeX" / "miktex" / "bin" / "x64",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "MiKTeX" / "miktex" / "bin" / "x64",
    ]
    for root in candidate_roots:
        candidate = root / executable
        if candidate.exists():
            return candidate

    return settings.latex_engine


def _build_latex_env(engine: Path | str) -> dict[str, str]:
    env = os.environ.copy()
    engine_path = Path(engine)
    if engine_path.exists():
        env["PATH"] = str(engine_path.parent) + os.pathsep + env.get("PATH", "")

    return env


def _build_latex_command(engine: Path | str, tex_filename: str) -> list[str]:
    command = [str(engine)]
    if sys.platform.startswith("win"):
        command.append("-disable-installer")
    command.extend(
        [
            "-interaction=nonstopmode",
            "-halt-on-error",
            tex_filename,
        ]
    )
    return command


def compile_pdf(resume: GeneratedResume) -> Path:
    engine = _resolve_latex_engine()
    engine_path = Path(engine)
    if not engine_path.exists() and shutil.which(str(engine)) is None:
        raise PdfServiceError(
            f"LaTeX-движок {settings.latex_engine} не найден. Скачайте LaTeX-файл или установите TeX Live/MiKTeX."
        )

    tex_path = save_latex_file(resume)
    command = _build_latex_command(engine, tex_path.name)
    pdf_path = tex_path.with_suffix(".pdf")
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except PermissionError:
            if pdf_path.stat().st_size > 0:
                return pdf_path
            raise

    try:
        completed = subprocess.run(
            command,
            cwd=settings.generated_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            env=_build_latex_env(engine),
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfServiceError("Сборка PDF превысила лимит времени.") from exc

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise PdfServiceError("LaTeX не смог собрать PDF. Проверьте .tex-файл и установленный движок.")

    return pdf_path


def compile_cover_letter_pdf(letter: GeneratedCoverLetter) -> Path:
    engine = _resolve_latex_engine()
    engine_path = Path(engine)
    if not engine_path.exists() and shutil.which(str(engine)) is None:
        raise PdfServiceError(
            f"LaTeX-движок {settings.latex_engine} не найден. Скачайте LaTeX-файл или установите TeX Live/MiKTeX."
        )

    tex_path = save_cover_letter_latex_file(letter)
    command = _build_latex_command(engine, tex_path.name)
    pdf_path = tex_path.with_suffix(".pdf")
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except PermissionError:
            if pdf_path.stat().st_size > 0:
                return pdf_path
            raise

    try:
        completed = subprocess.run(
            command,
            cwd=settings.generated_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            env=_build_latex_env(engine),
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfServiceError("Сборка PDF превысила лимит времени.") from exc

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise PdfServiceError("LaTeX не смог собрать PDF. Проверьте .tex-файл и установленный движок.")

    return pdf_path
