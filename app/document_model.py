"""The internal structured document model for OpenCV scan results.

Why this layer exists
---------------------
Structured scanner results can vary between implementations. Everything
downstream (the export writers, API responses, and Flutter client) reads this
stable model instead.

The parsing here is deliberately defensive -- it probes several known key spellings
and degrades to a coarser representation rather than raising, because a partially
recovered document is far more useful than a 500.

Coordinates
-----------
``bbox`` is ``[x0, y0, x1, y1]`` in **source-image pixels, origin top-left**
(the convention used by the scanner). Exporters convert to their own coordinate
space; nothing else re-interprets these numbers.

Confidence
----------
``confidence`` is the model's own score, propagated verbatim, and is ``None``
when the underlying model exposes no score for that element type. It is never
defaulted to 1.0 -- callers use it to flag low-confidence regions for user
correction, which only works if the absence of a score is visible as such.
"""

from __future__ import annotations

import datetime as dt
import uuid
from html.parser import HTMLParser
from typing import Any

from .logging_config import get_logger
from .schemas import DocElement, DocPage, StructuredDocument, TableCell, TableData

log = get_logger(__name__)

# Regions scoring below this are surfaced in ``lowConfidenceElementIds``.
LOW_CONFIDENCE_THRESHOLD = 0.75

ELEMENT_TYPES = (
    "title",
    "heading",
    "paragraph",
    "text",
    "list",
    "table",
    "figure",
    "chart",
    "formula",
    "caption",
    "header",
    "footer",
    "footnote",
    "reference",
    "seal",
    "number",
    "aside",
    "algorithm",
    "checkbox",
    "other",
)

# Structured layout labels -> our vocabulary.
_LABEL_MAP: dict[str, str] = {
    "doc_title": "title",
    "title": "title",
    "paragraph_title": "heading",
    "sub_title": "heading",
    "text": "paragraph",
    "plain text": "paragraph",
    "abstract": "paragraph",
    "content": "list",
    "list": "list",
    "table": "table",
    "table_title": "caption",
    "table_caption": "caption",
    "figure_title": "caption",
    "chart_title": "caption",
    "figure_caption": "caption",
    "image": "figure",
    "figure": "figure",
    "header_image": "figure",
    "footer_image": "figure",
    "chart": "chart",
    "formula": "formula",
    "equation": "formula",
    "formula_number": "formula",
    "reference": "reference",
    "reference_content": "reference",
    "footnote": "footnote",
    "header": "header",
    "footer": "footer",
    "seal": "seal",
    "number": "number",
    "aside_text": "aside",
    "algorithm": "algorithm",
}


def normalize_label(label: Any) -> str:
    if not label:
        return "other"
    key = str(label).strip().lower().replace("-", "_").replace(" ", "_")
    if key in _LABEL_MAP:
        return _LABEL_MAP[key]
    key2 = str(label).strip().lower()
    if key2 in _LABEL_MAP:
        return _LABEL_MAP[key2]
    return key if key in ELEMENT_TYPES else "other"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _first(source: dict, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(source, dict) and name in source and source[name] is not None:
            return source[name]
    return default


def _as_bbox(value: Any) -> list[float]:
    """Coerce a bbox or polygon into ``[x0, y0, x1, y1]``."""
    if value is None:
        return []
    try:
        # Polygon: [[x, y], ...]
        if (
            isinstance(value, (list, tuple))
            and value
            and isinstance(value[0], (list, tuple))
        ):
            xs = [float(p[0]) for p in value]
            ys = [float(p[1]) for p in value]
            return [min(xs), min(ys), max(xs), max(ys)]
        seq = [float(v) for v in value]  # type: ignore[union-attr]
        if len(seq) >= 4:
            x0, y0, x1, y1 = seq[0], seq[1], seq[2], seq[3]
            return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    except (TypeError, ValueError, IndexError):
        pass
    return []


def _as_polygon(value: Any) -> list[list[float]] | None:
    if value is None:
        return None
    try:
        if (
            isinstance(value, (list, tuple))
            and value
            and isinstance(value[0], (list, tuple))
        ):
            return [[float(p[0]), float(p[1])] for p in value]
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _to_plain(value: Any) -> Any:
    """Recursively convert numpy scalars/arrays to JSON-safe builtins."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "tolist"):  # numpy array / scalar
        try:
            return _to_plain(value.tolist())
        except Exception:  # pragma: no cover
            return str(value)
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(v) for v in value]
    return value


def result_to_dict(result: Any) -> dict:
    """Best-effort conversion of a scanner result object into a plain dict."""
    payload: Any = None

    for attr in ("json", "_json"):
        if hasattr(result, attr):
            try:
                payload = getattr(result, attr)
                break
            except Exception:  # pragma: no cover
                payload = None

    if payload is None:
        if isinstance(result, dict):
            payload = result
        else:
            try:
                payload = dict(result)  # BaseResult is Mapping-like
            except Exception:  # pragma: no cover
                log.warning("Could not convert result of type %s", type(result))
                return {}

    payload = _to_plain(payload)
    if isinstance(payload, dict) and "res" in payload and isinstance(payload["res"], dict):
        payload = payload["res"]
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# HTML table parsing (stdlib only -- no bs4/lxml dependency)
# ---------------------------------------------------------------------------
class _TableHtmlParser(HTMLParser):
    """Parse a ``<table>`` into a grid, honouring rowspan/colspan."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[TableCell] = []
        self._row = -1
        self._col = 0
        self._in_cell = False
        self._is_header = False
        self._buffer: list[str] = []
        self._rowspan = 1
        self._colspan = 1
        # (row, col) slots already claimed by a spanning cell above.
        self._occupied: set[tuple[int, int]] = set()
        self.row_count = 0
        self.column_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row += 1
            self._col = 0
            return
        if tag in ("td", "th"):
            attributes = {k.lower(): (v or "") for k, v in attrs}
            self._in_cell = True
            self._is_header = tag == "th"
            self._buffer = []
            self._rowspan = _safe_int(attributes.get("rowspan"), 1)
            self._colspan = _safe_int(attributes.get("colspan"), 1)
            while (self._row, self._col) in self._occupied:
                self._col += 1

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag not in ("td", "th") or not self._in_cell:
            return

        text = " ".join("".join(self._buffer).split())
        row = max(self._row, 0)
        self.cells.append(
            TableCell(
                row=row,
                col=self._col,
                rowSpan=self._rowspan,
                colSpan=self._colspan,
                text=text,
                isHeader=self._is_header,
            )
        )

        for r in range(row, row + self._rowspan):
            for c in range(self._col, self._col + self._colspan):
                self._occupied.add((r, c))

        self._col += self._colspan
        self.row_count = max(self.row_count, row + self._rowspan)
        self.column_count = max(self.column_count, self._col)
        self._in_cell = False
        self._buffer = []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(1, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


def parse_table_html(html: str | None) -> TableData:
    """Turn a scanner-provided ``pred_html`` value into structured cells."""
    if not html:
        return TableData()
    parser = _TableHtmlParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # pragma: no cover - malformed model output
        log.warning("Could not parse table HTML: %s", exc)
        return TableData(html=html)

    return TableData(
        rowCount=parser.row_count,
        columnCount=parser.column_count,
        cells=parser.cells,
        html=html,
    )


# ---------------------------------------------------------------------------
# Reading order
# ---------------------------------------------------------------------------
def infer_reading_order(elements: list[DocElement], page_width: float) -> None:
    """Assign ``readingOrder`` for multi-column layouts via a column-aware sort.

    Only used when the pipeline did not supply its own ordering index. Elements
    are bucketed into columns by horizontal centre, then read column by column,
    top to bottom -- which is what a two-column paper or a newspaper needs, and
    degrades to plain top-to-bottom for single-column pages.
    """
    if not elements:
        return

    width = page_width or max((e.bbox[2] for e in elements if len(e.bbox) == 4), default=0)
    if width <= 0:
        for index, element in enumerate(
            sorted(elements, key=lambda e: (e.bbox[1] if len(e.bbox) == 4 else 0))
        ):
            element.readingOrder = index
        return

    # Full-width elements (banners, page-spanning titles/tables) break the
    # column flow, so they are ordered purely by vertical position.
    spanning: list[DocElement] = []
    columnar: list[DocElement] = []
    for element in elements:
        if len(element.bbox) != 4:
            spanning.append(element)
            continue
        if (element.bbox[2] - element.bbox[0]) > width * 0.65:
            spanning.append(element)
        else:
            columnar.append(element)

    # Estimate the column count from the distribution of horizontal centres.
    centres = sorted(((e.bbox[0] + e.bbox[2]) / 2.0) for e in columnar if len(e.bbox) == 4)
    column_edges: list[float] = []
    if centres:
        gap_threshold = width * 0.18
        previous = centres[0]
        for centre in centres[1:]:
            if centre - previous > gap_threshold:
                column_edges.append((centre + previous) / 2.0)
            previous = centre

    def column_index(element: DocElement) -> int:
        if len(element.bbox) != 4:
            return 0
        centre = (element.bbox[0] + element.bbox[2]) / 2.0
        index = 0
        for edge in column_edges:
            if centre > edge:
                index += 1
        return index

    def top(element: DocElement) -> float:
        return element.bbox[1] if len(element.bbox) == 4 else 0.0

    # Interleave: spanning elements act as horizontal dividers.
    ordered: list[DocElement] = []
    for element in sorted(spanning, key=top):
        ordered.append(element)
    ordered.extend(sorted(columnar, key=lambda e: (column_index(e), top(e))))
    # Stable final pass so spanning headers land above the columns they precede.
    ordered.sort(key=lambda e: (top(e) // max(1.0, width * 0.02),))

    for index, element in enumerate(ordered):
        element.readingOrder = index


def infer_hierarchy(elements: list[DocElement]) -> None:
    """Populate ``parentId``/``childIds`` and heading ``level``.

    Two relationships are recovered:
      * containment -- a caption or cell fully inside a figure/table becomes its
        child;
      * document outline -- each element is parented to the most recent heading
        of a shallower level, giving titles -> headings -> body nesting.
    """
    by_order = sorted(elements, key=lambda e: e.readingOrder)

    # ---- Heading levels ----
    for element in by_order:
        if element.type == "title":
            element.level = 1
        elif element.type == "heading":
            element.level = 2

    # ---- Containment (captions inside figures, etc.) ----
    def area(box: list[float]) -> float:
        if len(box) != 4:
            return 0.0
        return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])

    def contains(outer: list[float], inner: list[float]) -> bool:
        if len(outer) != 4 or len(inner) != 4:
            return False
        return (
            outer[0] <= inner[0] + 2
            and outer[1] <= inner[1] + 2
            and outer[2] >= inner[2] - 2
            and outer[3] >= inner[3] - 2
        )

    containers = [e for e in by_order if e.type in ("figure", "chart", "table")]
    for element in by_order:
        if element.type not in ("caption", "text", "paragraph", "formula"):
            continue
        best: DocElement | None = None
        for container in containers:
            if container.id == element.id:
                continue
            if contains(container.bbox, element.bbox):
                if best is None or area(container.bbox) < area(best.bbox):
                    best = container
        if best is not None:
            element.parentId = best.id
            best.childIds.append(element.id)

    # ---- Outline nesting for anything not already parented ----
    stack: list[DocElement] = []
    for element in by_order:
        level = element.level
        if level is not None:
            while stack and (stack[-1].level or 99) >= level:
                stack.pop()
            if stack:
                element.parentId = element.parentId or stack[-1].id
                stack[-1].childIds.append(element.id)
            stack.append(element)
        elif stack and element.parentId is None:
            element.parentId = stack[-1].id
            stack[-1].childIds.append(element.id)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def _ocr_lines(payload: dict) -> list[dict]:
    """Flatten ``overall_ocr_res`` into per-line dicts with text/score/poly."""
    ocr = _first(payload, "overall_ocr_res", "ocr_res", default={}) or {}
    if not isinstance(ocr, dict):
        return []

    texts = _first(ocr, "rec_texts", "texts", default=[]) or []
    scores = _first(ocr, "rec_scores", "scores", default=[]) or []
    polys = _first(ocr, "rec_polys", "dt_polys", "rec_boxes", "boxes", default=[]) or []

    lines: list[dict] = []
    for index, text in enumerate(texts):
        score = scores[index] if index < len(scores) else None
        poly = polys[index] if index < len(polys) else None
        lines.append(
            {
                "text": str(text),
                "score": float(score) if isinstance(score, (int, float)) else None,
                "bbox": _as_bbox(poly),
                "polygon": _as_polygon(poly),
            }
        )
    return lines


def _centre(box: list[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _score_for_region(bbox: list[float], lines: list[dict]) -> float | None:
    """Mean OCR score of the text lines whose centres fall in ``bbox``."""
    if len(bbox) != 4:
        return None
    collected = [
        line["score"]
        for line in lines
        if line["score"] is not None
        and len(line["bbox"]) == 4
        and bbox[0] <= _centre(line["bbox"])[0] <= bbox[2]
        and bbox[1] <= _centre(line["bbox"])[1] <= bbox[3]
    ]
    if not collected:
        return None
    return round(sum(collected) / len(collected), 4)


def normalize_page(
    raw: Any,
    *,
    page_number: int,
    page_width: float,
    page_height: float,
    rotation: float = 0.0,
    image_ref: str | None = None,
    warnings: list[str] | None = None,
) -> DocPage:
    """Convert one structured page result into a :class:`DocPage`."""
    payload = result_to_dict(raw)
    page = DocPage(
        pageNumber=page_number,
        width=float(page_width),
        height=float(page_height),
        rotation=float(rotation),
        imageRef=image_ref,
        warnings=list(warnings or []),
    )

    if not payload:
        page.warnings.append(
            "The OCR pipeline returned no parsable result for this page."
        )
        return page

    lines = _ocr_lines(payload)
    elements: list[DocElement] = []
    had_explicit_order = False

    # ---- Preferred source: parsing_res_list (labelled blocks in reading order)
    blocks = _first(payload, "parsing_res_list", "parsing_result", "layout_parsing_result")
    if isinstance(blocks, list) and blocks:
        for position, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            label = _first(block, "block_label", "label", "type", default="other")
            content = _first(block, "block_content", "content", "text", default="")
            bbox = _as_bbox(_first(block, "block_bbox", "bbox", "coordinate", "box"))
            order = _first(block, "index", "order", "reading_order")
            if isinstance(order, (int, float)):
                had_explicit_order = True

            element_type = normalize_label(label)
            text = str(content) if content is not None else ""

            element = DocElement(
                id=_new_id(),
                page=page_number,
                type=element_type,
                text=text or None,
                bbox=bbox,
                width=round(bbox[2] - bbox[0], 2) if len(bbox) == 4 else 0.0,
                height=round(bbox[3] - bbox[1], 2) if len(bbox) == 4 else 0.0,
                rotation=0.0,
                confidence=_first(block, "score", "confidence")
                or _score_for_region(bbox, lines),
                readingOrder=int(order) if isinstance(order, (int, float)) else position,
                sourceImage=image_ref,
            )

            if element_type == "table":
                html = _first(block, "block_content", "pred_html", "html")
                if isinstance(html, str) and "<" in html:
                    element.html = html
                    element.table = parse_table_html(html)
                    element.text = None
            elements.append(element)

    # ---- Fallback: layout boxes + OCR lines ----
    if not elements:
        layout = _first(payload, "layout_det_res", "layout_result", default={}) or {}
        boxes = _first(layout, "boxes", "bboxes", default=[]) if isinstance(layout, dict) else []
        for position, box in enumerate(boxes or []):
            if not isinstance(box, dict):
                continue
            bbox = _as_bbox(_first(box, "coordinate", "bbox", "box"))
            element_type = normalize_label(_first(box, "label", "cls_name", default="other"))
            contained = [
                line["text"]
                for line in lines
                if len(line["bbox"]) == 4
                and len(bbox) == 4
                and bbox[0] <= _centre(line["bbox"])[0] <= bbox[2]
                and bbox[1] <= _centre(line["bbox"])[1] <= bbox[3]
            ]
            elements.append(
                DocElement(
                    id=_new_id(),
                    page=page_number,
                    type=element_type,
                    text="\n".join(contained) or None,
                    bbox=bbox,
                    width=round(bbox[2] - bbox[0], 2) if len(bbox) == 4 else 0.0,
                    height=round(bbox[3] - bbox[1], 2) if len(bbox) == 4 else 0.0,
                    confidence=_first(box, "score", "confidence")
                    or _score_for_region(bbox, lines),
                    readingOrder=position,
                    sourceImage=image_ref,
                )
            )

    # ---- Last resort: raw OCR lines as paragraphs ----
    if not elements and lines:
        page.warnings.append(
            "No layout structure was detected; falling back to raw text lines."
        )
        for position, line in enumerate(lines):
            bbox = line["bbox"]
            elements.append(
                DocElement(
                    id=_new_id(),
                    page=page_number,
                    type="paragraph",
                    text=line["text"],
                    bbox=bbox,
                    polygon=line["polygon"],
                    width=round(bbox[2] - bbox[0], 2) if len(bbox) == 4 else 0.0,
                    height=round(bbox[3] - bbox[1], 2) if len(bbox) == 4 else 0.0,
                    confidence=line["score"],
                    readingOrder=position,
                    sourceImage=image_ref,
                )
            )

    # ---- Standalone tables not represented in parsing_res_list ----
    table_results = _first(payload, "table_res_list", "table_result", default=[]) or []
    if isinstance(table_results, list):
        existing_tables = [e for e in elements if e.type == "table"]
        for table in table_results:
            if not isinstance(table, dict):
                continue
            html = _first(table, "pred_html", "html")
            bbox = _as_bbox(_first(table, "block_bbox", "table_bbox", "bbox"))
            matched = next(
                (
                    e
                    for e in existing_tables
                    if len(e.bbox) == 4 and len(bbox) == 4 and _iou(e.bbox, bbox) > 0.5
                ),
                None,
            )
            if matched is not None:
                if html and not matched.html:
                    matched.html = html
                    matched.table = parse_table_html(html)
                    matched.text = None
                continue
            if html:
                elements.append(
                    DocElement(
                        id=_new_id(),
                        page=page_number,
                        type="table",
                        html=html,
                        table=parse_table_html(html),
                        bbox=bbox,
                        width=round(bbox[2] - bbox[0], 2) if len(bbox) == 4 else 0.0,
                        height=round(bbox[3] - bbox[1], 2) if len(bbox) == 4 else 0.0,
                        confidence=_score_for_region(bbox, lines),
                        readingOrder=len(elements),
                        sourceImage=image_ref,
                    )
                )

    if not had_explicit_order:
        infer_reading_order(elements, page.width)
    else:
        elements.sort(key=lambda e: e.readingOrder)
        for index, element in enumerate(elements):
            element.readingOrder = index

    infer_hierarchy(elements)
    page.elements = elements

    markdown = _first(payload, "markdown", default=None)
    if isinstance(markdown, dict):
        text = _first(markdown, "markdown_texts", "text")
        if isinstance(text, str):
            page.markdown = text
    elif isinstance(markdown, str):
        page.markdown = markdown

    return page


def _iou(a: list[float], b: list[float]) -> float:
    if len(a) != 4 or len(b) != 4:
        return 0.0
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def build_document(
    pages: list[DocPage],
    *,
    source_filename: str | None = None,
    engine: str | None = None,
    document_id: str | None = None,
) -> StructuredDocument:
    """Assemble pages into a document and compute confidence aggregates."""
    scores = [
        element.confidence
        for page in pages
        for element in page.elements
        if element.confidence is not None
    ]
    mean_confidence = round(sum(scores) / len(scores), 4) if scores else None

    low_confidence = [
        element.id
        for page in pages
        for element in page.elements
        if element.confidence is not None
        and element.confidence < LOW_CONFIDENCE_THRESHOLD
    ]

    return StructuredDocument(
        documentId=document_id or uuid.uuid4().hex,
        sourceFilename=source_filename,
        pageCount=len(pages),
        generator="prism-backend",
        engine=engine,
        createdAt=dt.datetime.now(dt.timezone.utc),
        pages=pages,
        meanTextConfidence=mean_confidence,
        lowConfidenceElementIds=low_confidence,
    )


def page_plain_text(page: DocPage) -> str:
    """Reading-order plain text for one page (used by the simple OCR endpoints)."""
    parts: list[str] = []
    for element in sorted(page.elements, key=lambda e: e.readingOrder):
        if element.text:
            parts.append(element.text.strip())
        elif element.table is not None:
            for row in _table_rows(element.table):
                parts.append("\t".join(row))
    return "\n".join(p for p in parts if p)


def _table_rows(table: TableData) -> list[list[str]]:
    """Expand a cell list into a dense row/column grid."""
    if not table.cells:
        return []
    rows = table.rowCount or (max((c.row + c.rowSpan for c in table.cells), default=0))
    cols = table.columnCount or (max((c.col + c.colSpan for c in table.cells), default=0))
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.cells:
        if 0 <= cell.row < rows and 0 <= cell.col < cols:
            grid[cell.row][cell.col] = cell.text
    return grid


def table_rows(table: TableData) -> list[list[str]]:
    """Public alias -- used by the DOCX/XLSX/Markdown exporters."""
    return _table_rows(table)
