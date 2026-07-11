"""Phase 4: map article figure citations to detected panel images."""

from __future__ import annotations

import json
import re
import csv
import os
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path


FIGURE_CITATION_RE = re.compile(
    r"\b(?i:(?:Extended\s+Data\s+Fig\.?|Supplementary\s+Fig\.?|Figs?\.?|Figures?))"
    r"\s+"
    r"(?P<body>"
    r"\d+[A-Z]?(?:\s*(?:[,;]|and|&)\s*(?:\d+)?[A-Z])*(?:\s*[\-–]\s*[A-Z])?"
    r"(?:\s*(?:[,;]|and|&)\s*\d+[A-Z](?:\s*[\-–]\s*[A-Z])?)*"
    r")",
)

SECTION_HEADINGS = [
    "Abstract",
    "Introduction",
    "Results",
    "Discussion",
    "References",
    "Materials and methods",
    "Methods",
    "Figure legends",
    "Supplementary Materials",
]


@dataclass(frozen=True)
class SentenceRecord:
    section: str
    result_heading: str | None
    paragraph_index: int
    sentence_index: int
    sentence: str


def build_citation_map(
    article_text_path: Path,
    figure_panels_path: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    if not article_text_path.exists():
        raise FileNotFoundError(
            f"Missing {article_text_path}. Please run Phase 2 first so pdf2jc can "
            "create output/article_text.json from input/paper.pdf."
        )
    if not figure_panels_path.exists():
        raise FileNotFoundError(
            f"Missing {figure_panels_path}. Please run Phase 3 first so pdf2jc can "
            "create output/figure_panels.json from input/figs/."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    available_panels = load_available_panels(figure_panels_path)
    article_text = load_article_text(article_text_path)
    section_names = find_section_names(article_text)
    sentences = split_article_into_sentences(article_text)

    citations = []
    unparsed_formats = []
    citation_counter = 1
    for record in sentences:
        for match in FIGURE_CITATION_RE.finditer(record.sentence):
            raw_text = match.group(0).strip()
            normalized_panels = normalize_citation(raw_text)
            if not normalized_panels:
                unparsed_formats.append(raw_text)
                continue

            available_images = [
                available_panels[panel_id]
                for panel_id in normalized_panels
                if panel_id in available_panels
            ]
            missing_panels = [
                panel_id for panel_id in normalized_panels if panel_id not in available_panels
            ]
            citations.append(
                {
                    "citation_id": f"cit_{citation_counter:04d}",
                    "section": record.section,
                    "result_heading": record.result_heading,
                    "paragraph_index": record.paragraph_index,
                    "sentence_index": record.sentence_index,
                    "sentence": record.sentence,
                    "raw_citation_text": raw_text,
                    "normalized_panels": normalized_panels,
                    "available_panel_images": available_images,
                    "missing_panels": missing_panels,
                    "confidence": "high" if not missing_panels else "medium",
                }
            )
            citation_counter += 1

    payload = {
        "article_text_file": str(article_text_path),
        "figure_panels_file": str(figure_panels_path),
        "citations": citations,
        "unparsed_citation_formats": sorted(set(unparsed_formats)),
        "section_headings_found": section_names,
    }

    citation_map_path = output_dir / "citation_map.json"
    report_path = output_dir / "citation_mapping_report.md"
    citation_map_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_citation_report(payload=payload, output_path=report_path)
    write_citation_qc_outputs(payload=payload, output_dir=output_dir)
    return citation_map_path, report_path


def load_available_panels(figure_panels_path: Path) -> dict[str, str]:
    try:
        panel_payload = json.loads(figure_panels_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not read {figure_panels_path}. Please check that it is valid JSON. "
            f"Details: {exc}"
        ) from exc

    panels = {}
    for panel in panel_payload:
        panel_id = panel.get("panel_id")
        output_file = panel.get("output_file")
        if panel_id and output_file:
            panels[panel_id] = output_file
    return panels


def load_article_text(article_text_path: Path) -> str:
    try:
        payload = json.loads(article_text_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not read {article_text_path}. Please check that it is valid JSON. "
            f"Details: {exc}"
        ) from exc

    pages = payload.get("pages", [])
    return "\n\n".join(page.get("text", "") for page in pages).strip()


def find_section_names(article_text: str) -> list[str]:
    found = []
    lowered = article_text.lower()
    for heading in SECTION_HEADINGS:
        if re.search(rf"\b{re.escape(heading.lower())}\b", lowered):
            found.append(heading)
    return found


def split_article_into_sentences(article_text: str) -> list[SentenceRecord]:
    section_blocks = split_into_sections(article_text)
    records = []
    sentence_index = 0
    paragraph_index = 0
    for section_name, section_text in section_blocks:
        result_heading = None
        for paragraph in split_paragraphs(section_text):
            heading = infer_result_heading(paragraph)
            if section_name == "Results" and heading:
                result_heading = heading
                continue

            for sentence in split_sentences(paragraph):
                records.append(
                    SentenceRecord(
                        section=section_name,
                        result_heading=result_heading,
                        paragraph_index=paragraph_index,
                        sentence_index=sentence_index,
                        sentence=sentence,
                    )
                )
                sentence_index += 1
            paragraph_index += 1
    return records


def split_into_sections(article_text: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in article_text.splitlines()]
    current_section = "Unknown"
    blocks: list[tuple[str, list[str]]] = [(current_section, [])]
    for line in lines:
        if not line:
            blocks[-1][1].append("")
            continue
        matched_heading = canonical_section_heading(line)
        if matched_heading:
            current_section = matched_heading
            blocks.append((current_section, []))
            continue
        blocks[-1][1].append(line)
    return [(section, "\n".join(block_lines).strip()) for section, block_lines in blocks if block_lines]


def canonical_section_heading(line: str) -> str | None:
    normalized = re.sub(r"[^A-Za-z ]+", "", line).strip().lower()
    for heading in SECTION_HEADINGS:
        if normalized == heading.lower():
            return "Figure legends" if heading == "Figure legends" else heading
    if normalized in {"figures", "figure legends"}:
        return "Figure legends"
    return None


def split_paragraphs(section_text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section_text) if p.strip()]
    if paragraphs:
        return paragraphs
    return [section_text.strip()] if section_text.strip() else []


def infer_result_heading(paragraph: str) -> str | None:
    cleaned = " ".join(paragraph.split())
    if len(cleaned) > 120:
        return None
    if FIGURE_CITATION_RE.search(cleaned):
        return None
    if cleaned.endswith("."):
        return None
    if cleaned and cleaned[0].isupper():
        return cleaned
    return None


def split_sentences(paragraph: str) -> list[str]:
    normalized = " ".join(paragraph.split())
    if not normalized:
        return []
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", normalized)
    return [piece.strip() for piece in pieces if piece.strip()]


def normalize_citation(raw_citation: str) -> list[str]:
    text = raw_citation.replace("–", "-").replace("—", "-")
    prefix_match = re.search(
        r"(?:Extended\s+Data\s+Fig\.?|Supplementary\s+Fig\.?|Figs?\.?|Figures?)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if not prefix_match:
        return []

    body = prefix_match.group(1)
    body = re.split(r"[\)\].;:]", body, maxsplit=1)[0]
    body = re.sub(r"\s+", " ", body).strip()
    tokens = re.findall(r"\d+[A-Z]?(?:-[A-Z])?|[A-Z](?:-[A-Z])?|and|&|,", body)

    panel_ids: list[str] = []
    current_figure: int | None = None
    for token in tokens:
        token = token.strip()
        if not token or token.lower() == "and" or token in {",", "&"}:
            continue

        if re.fullmatch(r"\d+", token):
            current_figure = int(token)
            continue

        match = re.fullmatch(r"(?:(\d+))?([A-Z])(?:-([A-Z]))?", token)
        if not match:
            continue
        figure_number_text, start_letter, end_letter = match.groups()
        if figure_number_text is not None:
            current_figure = int(figure_number_text)
        if current_figure is None:
            continue

        if end_letter:
            panel_ids.extend(expand_panel_range(current_figure, start_letter, end_letter))
        else:
            panel_ids.append(f"Fig{current_figure}{start_letter}")

    return dedupe_preserving_order(panel_ids)


def expand_panel_range(figure_number: int, start_letter: str, end_letter: str) -> list[str]:
    start = ord(start_letter)
    end = ord(end_letter)
    if end < start:
        return [f"Fig{figure_number}{start_letter}"]
    return [f"Fig{figure_number}{chr(code)}" for code in range(start, end + 1)]


def dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def write_citation_qc_outputs(payload: dict, output_dir: Path) -> tuple[Path, Path]:
    csv_path = output_dir / "citation_qc_table.csv"
    html_path = output_dir / "citation_qc_table.html"
    rows = [qc_row_for_citation(citation) for citation in payload["citations"]]

    fieldnames = [
        "citation_id",
        "section",
        "result_heading",
        "paragraph_index",
        "sentence_index",
        "sentence",
        "raw_citation_text",
        "normalized_panels",
        "available_panel_images",
        "missing_panels",
        "confidence",
        "qc_status",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    html_path.write_text(
        build_citation_qc_html(payload=payload, rows=rows, output_dir=output_dir),
        encoding="utf-8",
    )
    return csv_path, html_path


def qc_row_for_citation(citation: dict) -> dict:
    return {
        "citation_id": citation["citation_id"],
        "section": citation["section"],
        "result_heading": citation.get("result_heading") or "",
        "paragraph_index": citation["paragraph_index"],
        "sentence_index": citation["sentence_index"],
        "sentence": citation["sentence"],
        "raw_citation_text": citation["raw_citation_text"],
        "normalized_panels": "; ".join(citation["normalized_panels"]),
        "available_panel_images": "; ".join(citation["available_panel_images"]),
        "missing_panels": "; ".join(citation["missing_panels"]),
        "confidence": citation["confidence"],
        "qc_status": qc_status_for_citation(citation),
    }


def qc_status_for_citation(citation: dict) -> str:
    return "needs_review" if suspicious_reasons(citation) else "pending_review"


def suspicious_reasons(citation: dict) -> list[str]:
    reasons = []
    normalized_panels = citation.get("normalized_panels", [])
    available_images = citation.get("available_panel_images", [])
    missing_panels = citation.get("missing_panels", [])
    figures = {panel_figure_id(panel_id) for panel_id in normalized_panels}

    if not normalized_panels:
        reasons.append("normalized panels are empty")
    if citation.get("raw_citation_text") and not available_images:
        reasons.append("raw citation exists but no panel matched")
    if missing_panels:
        reasons.append("missing panel images")
    if len(normalized_panels) > 4:
        reasons.append("citation has more than 4 panels")
    if len(figures) > 1:
        reasons.append("citation maps to panels from different figures")
    if citation.get("confidence") == "low":
        reasons.append("confidence is low")
    if mentions_whole_figure_with_partial_panels(citation):
        reasons.append("sentence mentions a whole figure while only panels are mapped")
    return reasons


def panel_figure_id(panel_id: str) -> str:
    match = re.match(r"^(Fig\d+)", panel_id)
    return match.group(1) if match else panel_id


def mentions_whole_figure_with_partial_panels(citation: dict) -> bool:
    normalized_panels = citation.get("normalized_panels", [])
    if not normalized_panels:
        return False
    panel_figures = {panel_figure_id(panel_id) for panel_id in normalized_panels}
    for figure_number in whole_figure_mentions(citation.get("sentence", "")):
        if f"Fig{figure_number}" in panel_figures:
            return True
    return False


def whole_figure_mentions(text: str) -> list[str]:
    mentions = []
    for match in re.finditer(
        r"\b(?:Fig\.?|Figure)\s+(\d+)\b(?!\s*[A-Z])(?!\s*[,;]\s*[A-Z])",
        text,
        re.IGNORECASE,
    ):
        mentions.append(match.group(1))
    return mentions


def build_citation_qc_html(payload: dict, rows: list[dict], output_dir: Path) -> str:
    cards = []
    citation_by_id = {citation["citation_id"]: citation for citation in payload["citations"]}
    for row in rows:
        citation = citation_by_id[row["citation_id"]]
        thumbnails = []
        for image_path in citation["available_panel_images"]:
            html_image_path = html_relative_image_path(image_path=image_path, output_dir=output_dir)
            thumbnails.append(
                "<figure>"
                f'<img src="{escape(html_image_path)}" alt="{escape(Path(image_path).stem)}">'
                f"<figcaption>{escape(Path(image_path).stem)}</figcaption>"
                "</figure>"
            )
        if not thumbnails:
            thumbnails.append('<p class="muted">No matched panel image.</p>')

        missing = ""
        if citation["missing_panels"]:
            missing = (
                '<p class="warning">Missing panels: '
                f'{escape(", ".join(citation["missing_panels"]))}</p>'
            )

        reasons = suspicious_reasons(citation)
        reason_block = ""
        if reasons:
            reason_items = "".join(f"<li>{escape(reason)}</li>" for reason in reasons)
            reason_block = f'<div class="reasons"><strong>Review flags</strong><ul>{reason_items}</ul></div>'

        heading = row["result_heading"] or "None"
        cards.append(
            '<section class="citation-card">'
            f'<div class="card-top"><h2>{escape(row["citation_id"])}</h2>'
            f'<span class="status {escape(row["qc_status"])}">{escape(row["qc_status"])}</span></div>'
            f'<p class="meta"><strong>Section:</strong> {escape(row["section"])} '
            f'&nbsp; <strong>Result heading:</strong> {escape(heading)}</p>'
            f'<p class="sentence">{escape(row["sentence"])}</p>'
            f'<p><strong>Raw citation:</strong> <code>{escape(row["raw_citation_text"])}</code></p>'
            f'<p><strong>Normalized panels:</strong> {escape(row["normalized_panels"] or "None")}</p>'
            f'<p><strong>Confidence:</strong> {escape(row["confidence"])}</p>'
            f"{missing}{reason_block}"
            f'<div class="thumb-grid">{"".join(thumbnails)}</div>'
            "</section>"
        )

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>pdf2jc Citation QC</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; background: #f7f8fa; }
    h1 { margin-bottom: 4px; }
    .subtitle { color: #52606d; margin-top: 0; }
    .citation-card { background: white; border: 1px solid #d9e2ec; border-radius: 8px; padding: 18px; margin: 18px 0; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05); }
    .card-top { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    .card-top h2 { margin: 0; font-size: 20px; }
    .status { border-radius: 999px; padding: 4px 10px; font-size: 13px; font-weight: 700; }
    .pending_review { background: #e0f2fe; color: #075985; }
    .needs_review { background: #fee2e2; color: #991b1b; }
    .meta { color: #52606d; }
    .sentence { font-size: 16px; line-height: 1.5; background: #f8fafc; border-left: 4px solid #627d98; padding: 12px; }
    code { background: #eef2f7; padding: 2px 5px; border-radius: 4px; }
    .warning { color: #9a3412; font-weight: 700; }
    .reasons { background: #fff7ed; border: 1px solid #fed7aa; padding: 10px 12px; border-radius: 6px; }
    .reasons ul { margin: 6px 0 0 20px; padding: 0; }
    .thumb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin-top: 14px; }
    figure { margin: 0; border: 1px solid #d9e2ec; border-radius: 6px; padding: 8px; background: #fff; }
    img { width: 100%; height: 150px; object-fit: contain; display: block; background: #ffffff; }
    figcaption { font-size: 13px; color: #52606d; margin-top: 6px; text-align: center; }
    .muted { color: #6b7280; }
  </style>
</head>
<body>
  <h1>pdf2jc Citation QC</h1>
  <p class="subtitle">Use this report to check whether each sentence really corresponds to the matched panel images.</p>
""" + "\n".join(cards) + """
</body>
</html>
"""


def html_relative_image_path(image_path: str, output_dir: Path) -> str:
    path = Path(image_path)
    if path.is_absolute():
        return str(path)
    return os.path.relpath(path, start=output_dir)


def write_citation_report(payload: dict, output_path: Path) -> None:
    citations = payload["citations"]
    section_counts = Counter(citation["section"] for citation in citations)
    unique_panels = sorted(
        {panel_id for citation in citations for panel_id in citation["normalized_panels"]}
    )
    multi_panel_count = sum(1 for citation in citations if len(citation["normalized_panels"]) > 1)
    missing_count = sum(1 for citation in citations if citation["missing_panels"])
    suspicious = [
        (citation, suspicious_reasons(citation))
        for citation in citations
        if suspicious_reasons(citation)
    ]

    lines = [
        "# Citation Mapping Report",
        "",
        "## Summary",
        "",
        f"- Total citation groups: {len(citations)}",
        f"- Total unique cited panels: {len(unique_panels)}",
        f"- Citations with multiple panels: {multi_panel_count}",
        f"- Citations with missing panel images: {missing_count}",
        f"- Suspicious mappings: {len(suspicious)}",
        "",
        "## Citations by Section",
        "",
    ]
    if section_counts:
        for section, count in sorted(section_counts.items()):
            lines.append(f"- {section}: {count}")
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Section Headings Found",
        "",
    ])
    if payload["section_headings_found"]:
        lines.extend(f"- {heading}" for heading in payload["section_headings_found"])
    else:
        lines.append("- None detected")

    lines.extend(["", "## Whole-Figure Citations Not Parsed", ""])
    if payload["unparsed_citation_formats"]:
        lines.extend(f"- `{item}`" for item in payload["unparsed_citation_formats"])
    else:
        lines.append("- None")

    lines.extend(["", "## Suspicious Mappings", ""])
    if suspicious:
        for citation, reasons in suspicious:
            lines.append(
                f"- {citation['citation_id']} `{citation['raw_citation_text']}`: "
                f"{'; '.join(reasons)}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Examples", ""])
    for citation in citations[:12]:
        lines.append(
            f"- {citation['citation_id']} `{citation['raw_citation_text']}` -> "
            f"{', '.join(citation['normalized_panels']) or 'none'}"
        )

    if missing_count:
        lines.extend(["", "## Missing Panels", ""])
        for citation in citations:
            if citation["missing_panels"]:
                lines.append(
                    f"- {citation['citation_id']} `{citation['raw_citation_text']}` "
                    f"missing {', '.join(citation['missing_panels'])}"
                )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
