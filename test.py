"""Run one image through the same pipeline used by the app.

Usage from the backend directory:
    python test.py
    python test.py path\to\your\photo.jpg

Outputs are written to backend/test_output/:
    processed.png   Cropped and cleaned page produced by OpenCV
    document.json   Structured scan result
    recognized.txt  Empty because this scanner does not perform OCR
    final.pdf       Clean scanned document
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from app.document_model import build_document, normalize_page, page_plain_text
from app.exporters.pdf_export import export_pdf
from app.ocr_support import make_workdir, prepare_page
from app.storage import safe_rmtree


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_INPUTS = (
    BACKEND_DIR / "input.jpg",
    BACKEND_DIR / "input.jpeg",
    BACKEND_DIR / "input.png",
)
OUTPUT_DIR = BACKEND_DIR / "test_output"


def find_input() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()

    for path in DEFAULT_INPUTS:
        if path.is_file():
            return path

    names = ", ".join(path.name for path in DEFAULT_INPUTS)
    raise FileNotFoundError(
        f"No input image found. Put a photo in {BACKEND_DIR} as {names}, "
        "or pass its path as an argument."
    )


def main() -> None:
    input_path = find_input()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    workdir = make_workdir("manual_test")

    try:
        prepared = prepare_page(input_path.read_bytes(), workdir, stem="page")
        if prepared.is_pdf:
            raise ValueError("This manual test expects an image, not a PDF.")

        processed_path = OUTPUT_DIR / "processed.png"
        processed_path.write_bytes(prepared.display_bytes)

        print(f"Input:      {input_path}")
        print(f"Processed:  {processed_path}")
        print(f"Dimensions: {int(prepared.width)}x{int(prepared.height)}")
        print("OpenCV-Document-Scanner completed; no OCR model is used.")
        page = normalize_page(
            {}, page_number=1, page_width=prepared.width,
            page_height=prepared.height, image_ref=None,
            warnings=list(prepared.warnings),
        )
        page.imageBase64 = base64.b64encode(prepared.display_bytes).decode("ascii")
        pages = [page]

        document = build_document(
            pages,
            source_filename=input_path.name,
            engine="OpenCV-Document-Scanner",
        )
        final_pdf = OUTPUT_DIR / "final.pdf"
        final_pdf.write_bytes(export_pdf(document))
        (OUTPUT_DIR / "document.json").write_text(
            document.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
        )
        (OUTPUT_DIR / "recognized.txt").write_text(
            "\n\n".join(page_plain_text(page) for page in pages).strip(),
            encoding="utf-8",
        )

        element_types = [element.type for page in pages for element in page.elements]
        print(f"Pages:      {len(pages)}")
        print(f"PDF:        {final_pdf}")
        print(f"JSON:       {OUTPUT_DIR / 'document.json'}")
        print(f"Text:       {OUTPUT_DIR / 'recognized.txt'}")
        print(f"Elements:   {', '.join(element_types) or 'none'}")
        print("Done. Open final.pdf to inspect the scanned document and search its text.")
    finally:
        safe_rmtree(workdir)


if __name__ == "__main__":
    main()
