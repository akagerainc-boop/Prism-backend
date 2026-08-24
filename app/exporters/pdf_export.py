"""Searchable-PDF reconstruction via reportlab.

The page image is drawn full-bleed, then every recognised text element is drawn
on top in **text render mode 3** (invisible). The result looks exactly like the
original scan but selects, copies and full-text-searches like a text document --
which is what "searchable PDF" means.

When a page carries no image, the text is drawn visibly instead, so the export
is still a readable document rather than a blank page.
"""

from __future__ import annotations

import io

from ..config import settings
from ..document_model import table_rows
from ..logging_config import get_logger
from ..schemas import DocElement, DocPage, StructuredDocument
from .images import page_image_bytes

log = get_logger(__name__)

# US Letter at 72 dpi -- only used when a page declares no dimensions at all.
_FALLBACK_SIZE = (612.0, 792.0)
_INVISIBLE = 3  # PDF text render mode: fill=no, stroke=no, clip=no

_font_registered: str | None = None


class ExportUnavailable(RuntimeError):
    """A required export library is not installed."""


def _import_reportlab():
    try:
        from reportlab.lib.utils import ImageReader  # type: ignore[import-not-found]
        from reportlab.pdfbase import pdfmetrics  # type: ignore
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore

        return canvas, ImageReader, pdfmetrics, TTFont
    except Exception as exc:
        raise ExportUnavailable(
            "reportlab is not installed. Run `pip install reportlab`."
        ) from exc


def _resolve_font(pdfmetrics, TTFont) -> str:
    """Register the configured Unicode TTF, falling back to Helvetica.

    Helvetica is Latin-1 only. Any document containing CJK, Cyrillic, Greek,
    Arabic, etc. needs a real Unicode font or reportlab raises on encode --
    point ``PDF_UNICODE_FONT_PATH`` at e.g. NotoSans-Regular.ttf.
    """
    global _font_registered
    if _font_registered is not None:
        return _font_registered

    path = settings.pdf_unicode_font_path
    if path:
        try:
            pdfmetrics.registerFont(TTFont("PrismUnicode", path))
            _font_registered = "PrismUnicode"
            log.info("Registered Unicode PDF font from %s", path)
            return _font_registered
        except Exception as exc:
            log.warning(
                "Could not register PDF_UNICODE_FONT_PATH=%s (%s) -- falling back "
                "to Helvetica; non-Latin text may be dropped from the text layer.",
                path,
                exc,
            )

    _font_registered = "Helvetica"
    return _font_registered


def _encodable(text: str, font: str) -> str:
    """Drop characters the chosen font cannot encode, rather than failing."""
    if font != "Helvetica":
        return text
    return text.encode("latin-1", "ignore").decode("latin-1")


def _element_lines(element: DocElement) -> list[str]:
    if element.text:
        return [line for line in element.text.splitlines() if line.strip()]
    if element.table is not None:
        return ["  ".join(row) for row in table_rows(element.table) if any(row)]
    return []


def _draw_invisible_text(
    pdf, element: DocElement, *, scale_x: float, scale_y: float, page_height: float,
    font: str,
) -> None:
    """Place the element's text at its original coordinates, invisibly."""
    lines = _element_lines(element)
    if not lines or len(element.bbox) != 4:
        return

    x0, y0, x1, y1 = element.bbox
    box_width = max((x1 - x0) * scale_x, 1.0)
    box_height = max((y1 - y0) * scale_y, 1.0)

    line_height = box_height / len(lines)
    font_size = max(1.0, min(line_height * 0.85, 72.0))

    text_object = pdf.beginText()
    text_object.setTextRenderMode(_INVISIBLE)
    text_object.setFont(font, font_size)

    for index, raw_line in enumerate(lines):
        line = _encodable(raw_line.strip(), font)
        if not line:
            continue

        # PDF's origin is bottom-left; image coordinates are top-left.
        baseline_from_top = (y0 * scale_y) + (index + 0.8) * line_height
        pdf_y = page_height - baseline_from_top
        text_object.setTextOrigin(x0 * scale_x, pdf_y)

        # Squeeze the glyphs horizontally so the invisible text tracks the
        # visible ink -- this is what makes selection rectangles line up.
        natural = pdf.stringWidth(line, font, font_size)
        if natural > 0:
            text_object.setHorizScale(max(10.0, min(100.0 * box_width / natural, 400.0)))
        else:
            text_object.setHorizScale(100.0)

        text_object.textLine(line)

    pdf.drawText(text_object)


def _draw_visible_text(
    pdf, page: DocPage, *, page_width: float, page_height: float, font: str
) -> None:
    """No page image: lay the text out as an ordinary readable document."""
    margin = 48.0
    y = page_height - margin
    leading = 14.0

    for element in sorted(page.elements, key=lambda e: e.readingOrder):
        if element.type in ("header", "footer"):
            continue
        for raw_line in _element_lines(element):
            line = _encodable(raw_line.strip(), font)
            if not line:
                continue
            size = 16.0 if element.type == "title" else (
                13.0 if element.type == "heading" else 10.5
            )
            pdf.setFont(font, size)

            # Naive wrap at the page width.
            while line:
                fitted = line
                while (
                    pdf.stringWidth(fitted, font, size) > page_width - 2 * margin
                    and " " in fitted
                ):
                    fitted = fitted.rsplit(" ", 1)[0]
                if y < margin:
                    pdf.showPage()
                    y = page_height - margin
                    pdf.setFont(font, size)
                pdf.drawString(margin, y, fitted)
                y -= leading if fitted != line else leading
                line = line[len(fitted) :].strip()
                if fitted == line:  # pragma: no cover - guard against no progress
                    break
        y -= 6.0


def export_pdf(document: StructuredDocument, **_: object) -> bytes:
    """Render the structured document to searchable-PDF bytes."""
    canvas, ImageReader, pdfmetrics, TTFont = _import_reportlab()
    font = _resolve_font(pdfmetrics, TTFont)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.setTitle(document.sourceFilename or "Prism document")
    pdf.setCreator("Prism Scanner backend")

    if not document.pages:
        pdf.setPageSize(_FALLBACK_SIZE)
        pdf.showPage()
        pdf.save()
        return buffer.getvalue()

    for page in document.pages:
        image_data = page_image_bytes(page)

        source_width = page.width or 0.0
        source_height = page.height or 0.0
        reader = None

        if image_data:
            try:
                reader = ImageReader(io.BytesIO(image_data))
                image_width, image_height = reader.getSize()
                source_width = source_width or float(image_width)
                source_height = source_height or float(image_height)
            except Exception as exc:  # pragma: no cover
                log.warning("Could not read page %s image: %s", page.pageNumber, exc)
                reader = None

        if source_width <= 0 or source_height <= 0:
            page_width, page_height = _FALLBACK_SIZE
            scale_x = scale_y = 1.0
        else:
            # 1 source pixel -> 1 PDF point keeps coordinates trivially mappable.
            page_width, page_height = source_width, source_height
            scale_x = scale_y = 1.0

        pdf.setPageSize((page_width, page_height))

        if reader is not None:
            try:
                pdf.drawImage(
                    reader, 0, 0, width=page_width, height=page_height, mask="auto"
                )
            except Exception as exc:  # pragma: no cover
                log.warning("Could not draw page %s image: %s", page.pageNumber, exc)
                reader = None

        if reader is not None:
            for element in sorted(page.elements, key=lambda e: e.readingOrder):
                _draw_invisible_text(
                    pdf,
                    element,
                    scale_x=scale_x,
                    scale_y=scale_y,
                    page_height=page_height,
                    font=font,
                )
        else:
            _draw_visible_text(
                pdf, page, page_width=page_width, page_height=page_height, font=font
            )

        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def export_clean_pdf(document: StructuredDocument, **_: object) -> bytes:
    """Reconstruct pages on white paper from OCR/layout coordinates.

    Unlike :func:`export_pdf`, this does not draw the camera scan as a
    background. Text and tables are rebuilt from structured output;
    figures, charts, seals, and formulas keep their detected visual region
    where the source page is available.
    """
    canvas, ImageReader, pdfmetrics, TTFont = _import_reportlab()
    font = _resolve_font(pdfmetrics, TTFont)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.setTitle(document.sourceFilename or "Prism document")
    pdf.setCreator("Prism Scanner backend")

    for page in document.pages:
        page_width = page.width or _FALLBACK_SIZE[0]
        page_height = page.height or _FALLBACK_SIZE[1]
        pdf.setPageSize((page_width, page_height))
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)

        for element in sorted(page.elements, key=lambda item: item.readingOrder):
            if len(element.bbox) != 4:
                continue
            x0, y0, x1, y1 = element.bbox
            box_width = max(x1 - x0, 1.0)
            box_height = max(y1 - y0, 1.0)
            pdf_y = page_height - y1

            if element.type in ("figure", "chart", "seal"):
                data = element_image_bytes(element, document)
                if data:
                    try:
                        pdf.drawImage(
                            ImageReader(io.BytesIO(data)),
                            x0,
                            pdf_y,
                            width=box_width,
                            height=box_height,
                            preserveAspectRatio=True,
                            anchor="c",
                            mask="auto",
                        )
                    except Exception as exc:  # pragma: no cover
                        log.debug("Could not draw visual element %s: %s", element.id, exc)
                continue

            lines = _element_lines(element)
            if not lines:
                continue
            if element.type == "title":
                size = min(24.0, max(12.0, box_height * 0.75))
            elif element.type == "heading":
                size = min(18.0, max(10.0, box_height * 0.72))
            else:
                size = min(16.0, max(6.0, box_height / max(len(lines), 1) * 0.8))
            pdf.setFillColorRGB(0, 0, 0)
            pdf.setFont(font, size)
            line_height = box_height / max(len(lines), 1)
            for line_index, raw_line in enumerate(lines):
                text = _encodable(raw_line.strip(), font)
                if not text:
                    continue
                baseline = page_height - y0 - (line_index + 0.8) * line_height
                natural = pdf.stringWidth(text, font, size)
                if natural > box_width:
                    pdf.saveState()
                    pdf.translate(x0, baseline)
                    pdf.scale(max(box_width / natural, 0.1), 1.0)
                    pdf.drawString(0, 0, text)
                    pdf.restoreState()
                else:
                    pdf.drawString(x0, baseline, text)

            if element.type == "table":
                pdf.setStrokeColorRGB(0.55, 0.55, 0.55)
                pdf.rect(x0, pdf_y, box_width, box_height, fill=0, stroke=1)
                if element.table is not None:
                    rows = max(element.table.rowCount, 1)
                    columns = max(element.table.columnCount, 1)
                    for row in range(1, rows):
                        y = pdf_y + box_height * row / rows
                        pdf.line(x0, y, x1, y)
                    for column in range(1, columns):
                        x = x0 + box_width * column / columns
                        pdf.line(x, pdf_y, x, pdf_y + box_height)

        pdf.showPage()

    if not document.pages:
        pdf.setPageSize(_FALLBACK_SIZE)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
