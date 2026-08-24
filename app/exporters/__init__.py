"""Reconstruct a :class:`~app.schemas.StructuredDocument` into real file formats.

Each writer preserves what the source format can express:
  * ``pdf``      -- page image + an invisible text layer at original coordinates
                    (a *searchable* PDF: looks like the scan, selects like text)
  * ``docx``     -- headings, paragraphs, tables and embedded images
  * ``markdown`` -- headings, lists, tables, image references
  * ``xlsx``     -- every detected table, one sheet per table
"""

from __future__ import annotations

from .docx_export import export_docx
from .markdown_export import export_markdown
from .pdf_export import export_clean_pdf, export_pdf
from .xlsx_export import export_xlsx

__all__ = ["export_clean_pdf", "export_docx", "export_markdown", "export_pdf", "export_xlsx"]

# format -> (writer, media type, file extension)
EXPORTERS = {
    "pdf": (export_pdf, "application/pdf", "pdf"),
    "docx": (
        export_docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "markdown": (export_markdown, "text/markdown; charset=utf-8", "md"),
    "xlsx": (
        export_xlsx,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
}
