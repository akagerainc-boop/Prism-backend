"""Merged, paginated "book" PDF -- the output of continuous scan mode.

The user scans page after page; this turns the batch into one finished document:
every page keeps its original image content, pages appear in the order they were
submitted, and a ``Page X of N`` footer is centred at the bottom of each.

Page geometry: pages are normalised to a single sheet size (A4 portrait by
default) with each scan scaled to fit and centred, rather than each PDF page
inheriting its source image's pixel dimensions. A book whose pages are all
different sizes prints and reads badly, and phone scans of the same physical
page routinely differ by a few pixels.
"""

from __future__ import annotations

import io
from pathlib import Path

from .logging_config import get_logger

log = get_logger(__name__)

# A4 portrait in points.
A4_PORTRAIT = (595.276, 841.890)

_FOOTER_FONT = "Helvetica"
_FOOTER_SIZE = 9.0
_FOOTER_MARGIN = 28.0  # distance from the bottom edge to the footer baseline
_SIDE_MARGIN = 24.0
_TOP_MARGIN = 24.0


class BookBuildError(RuntimeError):
    """The merged PDF could not be produced."""


def _import_reportlab():
    try:
        from reportlab.lib.utils import ImageReader  # type: ignore[import-not-found]
        from reportlab.pdfgen import canvas  # type: ignore

        return canvas, ImageReader
    except Exception as exc:
        raise BookBuildError(
            "reportlab is not installed. Run `pip install reportlab`."
        ) from exc


def build_book_pdf(
    pages: list[bytes],
    output_path: str | Path,
    *,
    page_size: tuple[float, float] = A4_PORTRAIT,
    title: str | None = None,
    number_pages: bool = True,
) -> Path:
    """Write ``pages`` (encoded image bytes, in order) to one paginated PDF.

    Returns the path written. Raises :class:`BookBuildError` if no page could be
    rendered -- an empty or broken PDF is worse than a clear failure.
    """
    if not pages:
        raise BookBuildError("No pages were supplied.")

    canvas, ImageReader = _import_reportlab()

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    page_width, page_height = page_size
    total = len(pages)

    pdf = canvas.Canvas(str(destination), pagesize=page_size)
    pdf.setTitle(title or "Prism scan")
    pdf.setCreator("Prism Scanner backend")

    rendered = 0
    for index, data in enumerate(pages, start=1):
        try:
            reader = ImageReader(io.BytesIO(data))
            image_width, image_height = reader.getSize()
        except Exception as exc:
            log.warning("Skipping unreadable page %d in book build: %s", index, exc)
            image_width = image_height = 0
            reader = None

        if reader is not None and image_width > 0 and image_height > 0:
            # Reserve room for the footer so the number never sits on the scan.
            available_width = page_width - 2 * _SIDE_MARGIN
            available_height = page_height - _TOP_MARGIN - (_FOOTER_MARGIN + 14.0)

            scale = min(available_width / image_width, available_height / image_height)
            draw_width = image_width * scale
            draw_height = image_height * scale
            x = (page_width - draw_width) / 2.0
            y = (page_height - draw_height) / 2.0 + (_FOOTER_MARGIN / 2.0)

            try:
                pdf.drawImage(
                    reader, x, y, width=draw_width, height=draw_height, mask="auto"
                )
                rendered += 1
            except Exception as exc:
                log.warning("Could not draw page %d: %s", index, exc)

        if number_pages:
            pdf.setFont(_FOOTER_FONT, _FOOTER_SIZE)
            pdf.setFillGray(0.35)
            pdf.drawCentredString(
                page_width / 2.0, _FOOTER_MARGIN, f"Page {index} of {total}"
            )
            pdf.setFillGray(0.0)

        pdf.showPage()

    if rendered == 0:
        raise BookBuildError("None of the submitted pages could be rendered.")

    pdf.save()
    log.info(
        "Built book PDF at %s (%d/%d pages rendered)", destination, rendered, total
    )
    return destination
