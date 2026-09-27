from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import struct
import xml.sax.saxutils as saxutils
import zipfile


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
OUT = DOCS / "TZ_CVService_Web_v3.docx"


IMG_FILES = [
    ("uml_sequence.png", "UML диаграмма последовательности (пользователь — сервер — БД — DeepSeek)."),
    ("erd_3nf.png", "ERD диаграмма БД (3НФ)."),
    ("class_diagram.png", "UML диаграмма классов серверной части."),
]


def esc(text: str) -> str:
    return saxutils.escape(text)


def read_png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"Не PNG: {path}")
        length = struct.unpack(">I", f.read(4))[0]
        if f.read(4) != b"IHDR":
            raise ValueError(f"Некорректный PNG (IHDR): {path}")
        data = f.read(length)
        width, height = struct.unpack(">II", data[:8])
        return width, height


def paragraph(text: str, *, bold: bool = False, sz: int | None = None) -> str:
    rpr = ""
    if bold or sz:
        parts: list[str] = []
        if bold:
            parts.append("<w:b/>")
        if sz:
            parts.append(f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>')
        rpr = f"<w:rPr>{''.join(parts)}</w:rPr>"
    return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'


def code_paragraph(text: str) -> str:
    return (
        "<w:p><w:r>"
        '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>'
        f'<w:t xml:space="preserve">{esc(text)}</w:t>'
        "</w:r></w:p>"
    )


def image_paragraph(rid: str, docpr_id: int, name: str, cx: int, cy: int) -> str:
    return f"""<w:p>
  <w:r>
    <w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
        <wp:extent cx="{cx}" cy="{cy}"/>
        <wp:effectExtent l="0" t="0" r="0" b="0"/>
        <wp:docPr id="{docpr_id}" name="{esc(name)}"/>
        <wp:cNvGraphicFramePr>
          <a:graphicFrameLocks noChangeAspect="1" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>
        </wp:cNvGraphicFramePr>
        <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
              <pic:nvPicPr>
                <pic:cNvPr id="0" name="{esc(name)}"/>
                <pic:cNvPicPr/>
              </pic:nvPicPr>
              <pic:blipFill>
                <a:blip r:embed="{rid}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>
                <a:stretch><a:fillRect/></a:stretch>
              </pic:blipFill>
              <pic:spPr>
                <a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
              </pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>"""


def build_docx() -> None:
    body: list[str] = []

    body.append(paragraph("ТЕХНИЧЕСКОЕ ЗАДАНИЕ (v2)", bold=True, sz=34))
    body.append(
        paragraph(
            "Проект: CVService Web — система аналитики соответствия кандидата вакансии и генерации адаптированного резюме.",
            sz=24,
        )
    )
    body.append(paragraph(f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}", sz=22))
    body.append(paragraph(""))

    body.append(paragraph("1. Цель и область проекта", bold=True, sz=30))
    for line in [
        "Система помогает соискателю оценить соответствие вакансии и подготовить адаптированное резюме.",
        "Поддерживаются вакансии на русском и английском языках (RU/EN).",
        "Ограничение модели: один email соответствует одному профилю кандидата.",
        "Аналитика и генерация выполняются строго по паре (profile_id, vacancy_id).",
        "Результат включает match/scoring, объяснимые рекомендации и экспортируемое резюме.",
    ]:
        body.append(paragraph(f"• {line}"))

    body.append(paragraph("2. Функциональные требования", bold=True, sz=30))
    for line in [
        "FR-01: Регистрация/логин, JWT-cookie, защита маршрутов.",
        "FR-02: Заполнение профиля только через форму (без загрузки JSON профиля).",
        "FR-03: Создание вакансии по URL ИЛИ raw_text.",
        "FR-04: Сохранение сырой вакансии в файл storage/vacancy_raw/<hash>.txt.",
        "FR-05: В БД хранится только относительный путь raw_data_path к сырому файлу.",
        "FR-06: Извлечение требований must/nice через DeepSeek в строгом JSON-формате.",
        "FR-07: Аналитика match, скоринг и evidence-показатели по паре (profile_id, vacancy_id).",
        "FR-08: Рекомендации двух классов: resume_edit и learn_and_practice.",
        "FR-09: Генерация resume_json с анти-галлюцинационными ограничениями.",
        "FR-10: Правки резюме естественным языком с версионированием.",
        "FR-11: Экспорт результата в PDF или LaTeX (.tex) на выбор пользователя.",
    ]:
        body.append(paragraph(f"• {line}"))

    body.append(paragraph("3. Нефункциональные требования", bold=True, sz=30))
    for line in [
        "NFR-01: Валидация входных данных (email, URL, обязательные поля).",
        "NFR-02: Логи ошибок API и LLM-вызовов.",
        "NFR-03: Доступ только к данным текущего пользователя.",
        "NFR-04: Хранение паролей в хэше.",
        "NFR-05: Отслеживаемость аналитики через снимки расчётов.",
    ]:
        body.append(paragraph(f"• {line}"))

    body.append(paragraph("4. Структура БД (3НФ)", bold=True, sz=30))
    for line in [
        "users(id PK, email UNIQUE, password_hash, created_at)",
        "profiles(id PK, user_id FK UNIQUE, full_name, target_position, email, phone, github_url, linkedin_url, summary_text, created_at, updated_at)",
        "vacancies(id PK, user_id FK, title, source_url, raw_data_path, created_at, updated_at)",
        "vacancy_requirements(id PK, vacancy_id FK, type(must|nice), skill_norm, UNIQUE(vacancy_id,type,skill_norm), created_at)",
        "analysis_snapshots(id PK, user_id FK, profile_id FK, vacancy_id FK, must_pct, nice_pct, total_pct, evidence_*, created_at)",
        "recommendations(id PK, analysis_id FK, class(resume_edit|learn_and_practice), requirement_key, reason, expected_impact, priority_score, created_at)",
        "generated_resumes(id PK, user_id FK, profile_id FK, vacancy_id FK, resume_json JSONB, latex_source, pdf_path, version_no, created_at)",
    ]:
        body.append(paragraph(f"• {line}"))

    body.append(paragraph("5. Формулы аналитики", bold=True, sz=30))
    for line in [
        "evidence_r = min(1, 0.65*exact_skill_match + 0.35*related_skill_match + 0.25*min(text_hits/3,1) + 0.10*context_score)",
        "must_pct = average(evidence_r по must-требованиям) * 100",
        "nice_pct = average(evidence_r по nice-требованиям) * 100",
        "total_pct = 0.75*must_pct + 0.25*nice_pct, если есть оба класса требований",
        "если есть только must или только nice, total_pct равен проценту этого класса",
        "resume_edit: факт есть, но плохо представлен/подтверждён",
        "learn_and_practice: факта или практического опыта недостаточно",
    ]:
        body.append(code_paragraph(line))

    body.append(paragraph("6. Структура проекта", bold=True, sz=30))
    for line in [
        "apps/web/main.py",
        "apps/web/routes/          # auth, profile, vacancies, result",
        "apps/web/services/        # extraction, match, recommendations, generation, export",
        "apps/api/db/models.py     # ORM-модели",
        "apps/api/repositories/    # слой доступа к данным",
        "core/config.py, core/security.py, core/utils.py",
        "storage/vacancy_raw/      # сырой текст вакансий (<hash>.txt)",
        "storage/generated/        # PDF/LaTeX",
        "tests/",
        "docs/assets/              # PNG диаграммы",
    ]:
        body.append(code_paragraph(line))

    body.append(paragraph("7. Диаграммы", bold=True, sz=30))

    rel_items: list[tuple[str, str]] = []
    image_parts: list[tuple[Path, str]] = []
    next_rid = 2
    next_docpr = 1
    max_cx = int(6.5 * 914400)
    pic_index = 1

    for fname, caption in IMG_FILES:
        img_path = ASSETS / fname
        if not img_path.exists():
            continue
        width, height = read_png_size(img_path)
        cx = int(width * 9525)
        cy = int(height * 9525)
        if cx > max_cx:
            scale = max_cx / cx
            cx = int(cx * scale)
            cy = int(cy * scale)

        rid = f"rId{next_rid}"
        rel_items.append((rid, f"media/{fname}"))
        image_parts.append((img_path, fname))

        body.append(image_paragraph(rid, next_docpr, fname, cx, cy))
        body.append(paragraph(f"Рисунок {pic_index}. {caption}"))
        body.append(paragraph(""))

        next_rid += 1
        next_docpr += 1
        pic_index += 1

    body.append(paragraph("8. Критерии готовности (DoD)", bold=True, sz=30))
    for line in [
        "Пользователь проходит полный цикл: профиль -> вакансия -> анализ -> рекомендации -> генерация -> экспорт.",
        "Raw текст вакансии хранится в файловом хранилище, в БД хранится relative_path.",
        "Аналитика возвращает match, evidence и рекомендации двух классов.",
        "Резюме генерируется без вымышленных фактов и поддерживает пользовательские правки.",
    ]:
        body.append(paragraph(f"• {line}"))

    body.append(paragraph(""))
    body.append(paragraph("Конец документа.", sz=20))

    body_xml = "\n".join(body)

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:w10="urn:schemas-microsoft-com:office:word"
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
 xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
 mc:Ignorable="w14 wp14">
  <w:body>
    {body_xml}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
      <w:cols w:space="708"/>
      <w:docGrid w:linePitch="360"/>
    </w:sectPr>
  </w:body>
</w:document>"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
        <w:sz w:val="22"/>
        <w:szCs w:val="22"/>
      </w:rPr>
    </w:rPrDefault>
    <w:pPrDefault><w:pPr/></w:pPrDefault>
  </w:docDefaults>
</w:styles>"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

    pkg_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

    rel_lines = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    ]
    for rid, target in rel_items:
        rel_lines.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>'
        )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rel_lines)
        + "</Relationships>"
    )

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>ТЗ CVService Web v2</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>"""

    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Microsoft Office Word</Application>
</Properties>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", pkg_rels)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        for path, fname in image_parts:
            zf.write(path, f"word/media/{fname}")

    print(f"OK: {OUT}")


if __name__ == "__main__":
    build_docx()
