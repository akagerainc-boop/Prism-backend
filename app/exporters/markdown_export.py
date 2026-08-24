"""Markdown serialiser for the structured document model.

Hand-written (no library) so the mapping from element type to Markdown is
explicit and tunable.
"""

from __future__ import annotations

from ..document_model import table_rows
from ..schemas import DocElement, DocPage, StructuredDocument


def _escape_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _table_to_markdown(element: DocElement) -> str:
    if element.table is None:
        return ""

    rows = table_rows(element.table)
    if not rows:
        # Nothing structured survived; keep the raw HTML so no content is lost.
        return element.html or ""

    width = max(len(row) for row in rows)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]

    # Treat the first row as the header when the model marked it as such, or
    # when every one of its cells is non-empty (the usual case for real tables).
    header_flagged = any(c.isHeader and c.row == 0 for c in element.table.cells)
    has_header = header_flagged or all(cell.strip() for cell in padded[0])

    lines: list[str] = []
    if has_header:
        header, body = padded[0], padded[1:]
    else:
        header, body = [""] * width, padded

    lines.append("| " + " | ".join(_escape_cell(c) for c in header) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in body:
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")

    return "\n".join(lines)


def _element_to_markdown(element: DocElement) -> str:
    text = (element.text or "").strip()

    element_type = element.type
    if element_type == "title":
        return f"# {text}" if text else ""
    if element_type == "heading":
        level = min(max(element.level or 2, 2), 6)
        return f"{'#' * level} {text}" if text else ""
    if element_type == "table":
        return _table_to_markdown(element)
    if element_type in ("figure", "chart"):
        ref = element.sourceImage or ""
        label = "Chart" if element_type == "chart" else "Figure"
        caption = text or label
        return f"![{caption}]({ref})" if ref else f"_[{label}: {caption}]_"
    if element_type == "formula":
        if not text:
            return ""
        # Structured scanners may emit LaTeX for formula regions.
        return f"$$\n{text}\n$$"
    if element_type == "list":
        if not text:
            return ""
        return "\n".join(
            f"- {line.strip()}" for line in text.splitlines() if line.strip()
        )
    if element_type == "caption":
        return f"*{text}*" if text else ""
    if element_type in ("footnote", "reference"):
        return f"> {text}" if text else ""
    if element_type == "seal":
        return f"_[Seal/stamp: {text}]_" if text else "_[Seal/stamp detected]_"
    if element_type in ("header", "footer"):
        # Running heads are page furniture, not document content -- they are
        # kept in the JSON model but omitted from the prose reconstruction.
        return ""
    return text


def _page_to_markdown(page: DocPage, *, include_page_breaks: bool) -> str:
    # Prefer the scanner's own Markdown when it supplied it: it resolves
    # inline formulas and cross-column flow better than a per-element mapping.
    if page.markdown and page.markdown.strip():
        body = page.markdown.strip()
    else:
        chunks = [
            _element_to_markdown(element)
            for element in sorted(page.elements, key=lambda e: e.readingOrder)
        ]
        body = "\n\n".join(chunk for chunk in chunks if chunk.strip())

    if include_page_breaks:
        return f"<!-- page {page.pageNumber} -->\n\n{body}"
    return body


def export_markdown(
    document: StructuredDocument, *, include_page_breaks: bool = True, **_: object
) -> bytes:
    """Serialise the document to UTF-8 Markdown bytes."""
    parts = [
        _page_to_markdown(page, include_page_breaks=include_page_breaks)
        for page in document.pages
    ]
    text = "\n\n---\n\n".join(part for part in parts if part.strip())
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")
