"""Extract article text from a PDF using PyMuPDF."""

from __future__ import annotations

import json
from pathlib import Path

import fitz


def extract_pdf_text(pdf_path: Path, output_dir: Path) -> Path:
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF not found: {pdf_path}. Please put your paper at input/paper.pdf "
            "or pass --pdf with the correct file path."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    article_data = {
        "source_pdf": str(pdf_path),
        "page_count": 0,
        "pages": [],
    }

    with fitz.open(pdf_path) as document:
        article_data["page_count"] = document.page_count

        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()

            article_data["pages"].append(
                {
                    "page_number": page_index,
                    "text": text,
                    "character_count": len(text),
                }
            )

    article_text_path = output_dir / "article_text.json"
    article_text_path.write_text(
        json.dumps(article_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return article_text_path


def extract_pdf(pdf_path: Path, output_dir: Path) -> Path:
    return extract_pdf_text(pdf_path=pdf_path, output_dir=output_dir)
