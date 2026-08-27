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
from .images import element_image_bytes, page_image_bytes

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
    if not font.startswith("Helvetica"):
        return text
    return text.encode("latin-1", "ignore").decode("latin-1")


def _element_lines(element: DocElement) -> list[str]:
    if element.text:
        return [line for line in element.text.splitlines() if line.strip()]
    if element.table is not None:
        return ["  ".join(row) for row in table_rows(element.table) if any(row)]
    return []


def _styled_font(font: str, *, bold: bool, italic: bool) -> str:
    """Pick a bold/italic variant of the base-14 Helvetica family.

    A registered Unicode TTF may not ship bold/italic variants, so styling
    is only applied on top of Helvetica -- a custom font stays regular
    rather than erroring on a missing variant.
    """
    if font != "Helvetica":
        return font
    if bold and italic:
        return "Helvetica-BoldOblique"
    if bold:
        return "Helvetica-Bold"
    if italic:
        return "Helvetica-Oblique"
    return "Helvetica"


def _hex_to_rgb(value: str | None) -> tuple[float, float, float] | None:
    if not value or len(value) != 7 or value[0] != "#":
        return None
    try:
        r = int(value[1:3], 16) / 255.0
        g = int(value[3:5], 16) / 255.0
        b = int(value[5:7], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return None


def _aligned_x(x0: float, box_width: float, natural_width: float, align: str | None) -> float:
    if align == "center":
        return x0 + max(0.0, (box_width - natural_width) / 2.0)
    if align == "right":
        return x0 + max(0.0, box_width - natural_width)
    return x0


def _draw_checkbox(
    pdf, *, checked: bool, x0: float, pdf_y: float, box_width: float, box_height: float,
) -> None:
    size = max(4.0, min(box_width, box_height))
    box_x = x0
    box_y = pdf_y + (box_height - size) / 2.0
    pdf.setStrokeColorRGB(0.2, 0.2, 0.2)
    pdf.setLineWidth(1.2)
    pdf.rect(box_x, box_y, size, size, fill=0, stroke=1)
    if checked:
        pdf.line(box_x + size * 0.18, box_y + size * 0.5, box_x + size * 0.42, box_y + size * 0.2)
        pdf.line(box_x + size * 0.42, box_y + size * 0.2, box_x + size * 0.85, box_y + size * 0.8)
    pdf.setLineWidth(1.0)
    pdf.setStrokeColorRGB(0.55, 0.55, 0.55)


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


def _render_formula_image(latex: str, box_width: float, box_height: float) -> bytes | None:
    """Render a LaTeX-ish formula to a transparent PNG via matplotlib's mathtext.

    mathtext (matplotlib's built-in math renderer) covers a wide common
    subset -- fractions, roots, sub/superscripts, Greek letters, sums,
    integrals -- without needing a real LaTeX installation. It cannot parse
    everything real LaTeX can (e.g. \\begin{...} environments); when it
    can't, this returns None and the caller falls back to drawing the raw
    text instead of fabricating a rendering.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # pragma: no cover - matplotlib not installed
        return None

    text = latex.strip()
    if not text:
        return None
    if not (text.startswith("$") and text.endswith("$")):
        text = f"${text}$"

    dpi = 200.0
    fig = plt.figure(
        figsize=(max(box_width, 40.0) / dpi, max(box_height, 20.0) / dpi), dpi=dpi
    )
    try:
        fig.text(
            0.5, 0.5, text,
            fontsize=max(8.0, box_height * 0.5),
            ha="center", va="center", color="black",
        )
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", transparent=True, bbox_inches="tight", pad_inches=0.02)
        return buffer.getvalue()
    except Exception as exc:
        log.debug("Could not render formula %r: %s", latex[:60], exc)
        return None
    finally:
        plt.close(fig)


def _draw_table_cells(
    pdf, table, *, x0: float, pdf_y: float, box_width: float, box_height: float, font: str,
) -> None:
    """Draw a real editable-looking table: grid lines plus each cell's own
    text inside its own cell rect (honouring row/col spans), instead of
    dumping a whole row as one joined line of text.
    """
    pdf.setStrokeColorRGB(0.55, 0.55, 0.55)
    pdf.rect(x0, pdf_y, box_width, box_height, fill=0, stroke=1)

    rows = max(table.rowCount or max((c.row + c.rowSpan for c in table.cells), default=1), 1)
    columns = max(
        table.columnCount or max((c.col + c.colSpan for c in table.cells), default=1), 1
    )
    row_height = box_height / rows
    col_width = box_width / columns

    for row in range(1, rows):
        y = pdf_y + box_height - row * row_height
        pdf.line(x0, y, x0 + box_width, y)
    for column in range(1, columns):
        x = x0 + column * col_width
        pdf.line(x, pdf_y, x, pdf_y + box_height)

    pdf.setFillColorRGB(0, 0, 0)
    for cell in table.cells:
        text = (cell.text or "").strip()
        if not text:
            continue
        row_span = max(cell.rowSpan, 1)
        col_span = max(cell.colSpan, 1)
        cell_x = x0 + cell.col * col_width
        cell_width = col_width * col_span
        cell_top = pdf_y + box_height - cell.row * row_height
        cell_height = row_height * row_span

        size = min(11.0, max(6.0, cell_height * 0.55))
        font_name = font
        if cell.isHeader:
            # Helvetica-Bold exists whenever the base font is Helvetica; a
            # registered Unicode TTF may not ship a bold variant, so headers
            # in that case stay regular weight rather than erroring.
            font_name = "Helvetica-Bold" if font == "Helvetica" else font
        pdf.setFont(font_name, size)

        line = _encodable(text, font)
        pad = 3.0
        baseline = cell_top - cell_height * 0.65
        available = max(cell_width - 2 * pad, 4.0)
        natural = pdf.stringWidth(line, font_name, size)
        if natural > available:
            pdf.saveState()
            pdf.translate(cell_x + pad, baseline)
            pdf.scale(max(available / natural, 0.1), 1.0)
            pdf.drawString(0, 0, line)
            pdf.restoreState()
        else:
            pdf.drawString(cell_x + pad, baseline, line)


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

    total_pages = len(document.pages)
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

            if element.type == "formula" and element.text:
                rendered = _render_formula_image(element.text, box_width, box_height)
                if rendered:
                    try:
                        pdf.drawImage(
                            ImageReader(io.BytesIO(rendered)),
                            x0,
                            pdf_y,
                            width=box_width,
                            height=box_height,
                            preserveAspectRatio=True,
                            anchor="c",
                            mask="auto",
                        )
                        continue
                    except Exception as exc:  # pragma: no cover
                        log.debug("Could not draw formula image %s: %s", element.id, exc)
                # mathtext couldn't parse it (or drawing failed) -- fall through
                # and draw the raw LaTeX source as plain text instead of
                # silently dropping the element.

            if element.type == "table" and element.table is not None and element.table.cells:
                _draw_table_cells(
                    pdf, element.table,
                    x0=x0, pdf_y=pdf_y, box_width=box_width, box_height=box_height,
                    font=font,
                )
                continue

            if element.type == "checkbox":
                _draw_checkbox(pdf, checked=bool(element.checked), x0=x0, pdf_y=pdf_y, box_width=box_width, box_height=box_height)
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

            font_name = _styled_font(font, bold=element.bold, italic=element.italic)
            fill_rgb = _hex_to_rgb(element.color) or (0.0, 0.0, 0.0)

            if element.highlightColor:
                highlight_rgb = _hex_to_rgb(element.highlightColor)
                if highlight_rgb:
                    pdf.setFillColorRGB(*highlight_rgb)
                    pdf.rect(x0, pdf_y, box_width, box_height, fill=1, stroke=0)

            pdf.setFillColorRGB(*fill_rgb)
            pdf.setFont(font_name, size)
            line_height = box_height / max(len(lines), 1)
            for line_index, raw_line in enumerate(lines):
                text = _encodable(raw_line.strip(), font_name)
                if not text:
                    continue
                baseline = page_height - y0 - (line_index + 0.8) * line_height
                natural = pdf.stringWidth(text, font_name, size)

                if natural > box_width:
                    pdf.saveState()
                    pdf.translate(x0, baseline)
                    pdf.scale(max(box_width / natural, 0.1), 1.0)
                    pdf.drawString(0, 0, text)
                    pdf.restoreState()
                    line_x0, line_width = x0, box_width
                else:
                    line_x0 = _aligned_x(x0, box_width, natural, element.align)
                    pdf.drawString(line_x0, baseline, text)
                    line_width = natural

                if element.underline:
                    pdf.line(line_x0, baseline - 1.5, line_x0 + line_width, baseline - 1.5)
                if element.strikethrough:
                    strike_y = baseline + size * 0.3
                    pdf.line(line_x0, strike_y, line_x0 + line_width, strike_y)
            pdf.setFillColorRGB(0, 0, 0)

        if total_pages > 1:
            pdf.setFont(font, 9.0)
            pdf.setFillColorRGB(0.35, 0.35, 0.35)
            pdf.drawCentredString(
                page_width / 2.0, 20.0, f"Page {page.pageNumber} of {total_pages}"
            )
            pdf.setFillColorRGB(0, 0, 0)

        pdf.showPage()

    if not document.pages:
        pdf.setPageSize(_FALLBACK_SIZE)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
