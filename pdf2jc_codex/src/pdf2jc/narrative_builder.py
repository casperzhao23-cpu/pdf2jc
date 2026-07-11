"""Phase 5: build narrative units, evidence units, and semantic slide objects."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .citation_mapper import load_article_text, split_paragraphs


SlideType = Literal["result", "background", "method", "discussion"]

SECTION_TITLE_PREFIXES = [
    "Development of a mouse model enabling reversible autophagy suppression",
    "Reversible changes in the proteome and transcriptome",
    "Reversible neuronal proteostasis and ultrastructural changes",
    "Restoration of neuronal function after autophagy rescue",
]


class Panel(BaseModel):
    figure_id: str
    panel_id: str
    bbox: list[int] = Field(default_factory=list)
    source_figure: str | None = None
    output_file: str
    detection_method: str | None = None
    confidence: str = "unknown"


class CitationGroup(BaseModel):
    citation_id: str
    section: str
    result_heading: str | None = None
    paragraph_index: int
    sentence_index: int
    sentence: str
    raw_citation_text: str
    normalized_panels: list[str] = Field(default_factory=list)
    available_panel_images: list[str] = Field(default_factory=list)
    missing_panels: list[str] = Field(default_factory=list)
    confidence: str = "unknown"


class NarrativeUnit(BaseModel):
    narrative_unit_id: str
    section_id: str
    section_title: str
    paragraph_index: int
    paragraph_index_0based: int
    paragraph_number_1based: int
    citation_ids: list[str]
    panel_ids: list[str]
    supporting_paragraph: str
    supporting_sentences: list[str]
    inferred_topic: str
    confidence: str
    needs_manual_review: bool


class EvidenceUnit(BaseModel):
    evidence_id: str
    section_id: str
    narrative_unit_id: str
    paragraph_index: int
    paragraph_index_0based: int
    paragraph_number_1based: int
    supporting_paragraph: str
    supporting_sentences: list[str]
    cited_panels: list[str]
    panel_image_paths: list[str]
    matched_figure_legend: str | None = None
    biological_claim: str
    experiment_type: str
    grouping_reason: str
    confidence: str
    citation_ids: list[str]


class SlideObject(BaseModel):
    slide_id: str
    slide_type: SlideType
    section_id: str
    section_title: str
    slide_subtitle: str
    narrative_unit_id: str
    evidence_unit_id: str
    paragraph_index: int
    paragraph_index_0based: int
    paragraph_number_1based: int
    evidence_ids: list[str]
    panel_ids: list[str]
    panel_image_paths: list[str]
    supporting_paragraph: str
    supporting_sentences: list[str]
    biological_claim: str
    experiment_type: str
    speaker_notes_placeholder: str
    layout_type: str
    grouping_reason: str
    source_citation_ids: list[str]
    confidence: str
    needs_manual_review: bool


class Phase5Result(BaseModel):
    narrative_units_path: str
    evidence_units_path: str
    slides_path: str
    slide_review_html_path: str
    slide_review_csv_path: str


def build_slide_objects(
    article_text_path: Path,
    figure_panels_path: Path,
    citation_map_path: Path,
    output_dir: Path,
) -> Phase5Result:
    validate_phase5_inputs(
        article_text_path=article_text_path,
        figure_panels_path=figure_panels_path,
        citation_map_path=citation_map_path,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    article_text = load_article_text(article_text_path)
    paragraphs = split_paragraphs(article_text)
    panels = load_panels(figure_panels_path)
    citations = load_citation_groups(citation_map_path)
    figure_legends = extract_figure_legends(article_text)

    narrative_units = build_narrative_units(citations=citations, paragraphs=paragraphs)
    evidence_units = build_evidence_units(
        narrative_units=narrative_units,
        citations=citations,
        panels=panels,
        figure_legends=figure_legends,
    )
    slides = build_slides(narrative_units=narrative_units, evidence_units=evidence_units)

    narrative_units_path = output_dir / "narrative_units.json"
    evidence_units_path = output_dir / "evidence_units.json"
    slides_path = output_dir / "slides.json"
    slide_review_html_path = output_dir / "slide_review.html"
    slide_review_csv_path = output_dir / "slide_review.csv"

    write_model_json(narrative_units_path, narrative_units)
    write_model_json(evidence_units_path, evidence_units)
    write_model_json(slides_path, slides)
    write_slide_review_csv(slide_review_csv_path, slides)
    write_slide_review_html(slide_review_html_path, slides)

    return Phase5Result(
        narrative_units_path=str(narrative_units_path),
        evidence_units_path=str(evidence_units_path),
        slides_path=str(slides_path),
        slide_review_html_path=str(slide_review_html_path),
        slide_review_csv_path=str(slide_review_csv_path),
    )


def validate_phase5_inputs(
    article_text_path: Path,
    figure_panels_path: Path,
    citation_map_path: Path,
) -> None:
    if not article_text_path.exists():
        raise FileNotFoundError(
            f"Missing {article_text_path}. Please run Phase 2 first to create "
            "output/article_text.json."
        )
    if not figure_panels_path.exists():
        raise FileNotFoundError(
            f"Missing {figure_panels_path}. Please run Phase 3 first to create "
            "output/figure_panels.json."
        )
    if not citation_map_path.exists():
        raise FileNotFoundError(
            f"Missing {citation_map_path}. Please run Phase 4 first with "
            "PYTHONPATH=src python -m pdf2jc diagnose-citations."
        )


def load_panels(figure_panels_path: Path) -> dict[str, Panel]:
    payload = read_json_file(figure_panels_path)
    return {item["panel_id"]: Panel(**item) for item in payload}


def load_citation_groups(citation_map_path: Path) -> list[CitationGroup]:
    payload = read_json_file(citation_map_path)
    citations = [CitationGroup(**item) for item in payload.get("citations", [])]
    return sorted(citations, key=lambda item: (item.paragraph_index, item.sentence_index))


def read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not read {path}. Please check that it is valid JSON.") from exc


def build_narrative_units(
    citations: list[CitationGroup],
    paragraphs: list[str],
) -> list[NarrativeUnit]:
    units: list[NarrativeUnit] = []
    current: list[CitationGroup] = []

    def flush() -> None:
        if not current:
            return
        units.append(make_narrative_unit(index=len(units) + 1, citations=current, paragraphs=paragraphs))
        current.clear()

    for citation in citations:
        if not current:
            current.append(citation)
            continue
        previous = current[-1]
        if should_start_new_narrative(previous=previous, current=citation):
            flush()
        current.append(citation)
    flush()
    assign_section_ids(units)
    return units


def should_start_new_narrative(previous: CitationGroup, current: CitationGroup) -> bool:
    if previous.paragraph_index != current.paragraph_index:
        return True
    if starts_embedded_results_heading(current.sentence):
        return True
    if starts_new_logical_paragraph(current.sentence):
        return True
    if current.sentence_index - previous.sentence_index > 10:
        return True
    if primary_figure(previous.normalized_panels) != primary_figure(current.normalized_panels):
        return not explicitly_compares_figures(previous) and not explicitly_compares_figures(current)
    return False


def starts_embedded_results_heading(sentence: str) -> bool:
    return any(sentence.startswith(heading) for heading in SECTION_TITLE_PREFIXES)


def starts_new_logical_paragraph(sentence: str) -> bool:
    starters = [
        "Next,",
        "Systemic analysis",
        "To investigate",
        "After autophagy suppression",
        "Gene set enrichment",
        "Most essential amino acids",
        "Consistent with",
        "Step",
        "Purkinje neuron",
        "Scanning electron microscopy",
        "Serial sectioning",
        "This phenotype",
        "The mice were pretrained",
        "To confirm",
        "Atg101-",
        "Because learning",
        "By contrast",
        "In the radial",
    ]
    return any(sentence.startswith(starter) for starter in starters)


def make_narrative_unit(
    index: int,
    citations: list[CitationGroup],
    paragraphs: list[str],
) -> NarrativeUnit:
    paragraph_index = index - 1
    section_title = infer_section_title(citations)
    supporting_sentences = unique_preserving_order([citation.sentence for citation in citations])
    panel_ids = unique_preserving_order(
        [panel_id for citation in citations for panel_id in citation.normalized_panels]
    )
    source_paragraph = paragraph_at_index(paragraphs, citations[0].paragraph_index)
    supporting_paragraph = compact_supporting_paragraph(
        source_paragraph=source_paragraph,
        supporting_sentences=supporting_sentences,
    )
    confidence = lowest_confidence([citation.confidence for citation in citations])
    needs_review = confidence != "high" or any(citation.missing_panels for citation in citations)
    inferred_topic = summarize_experiment(supporting_sentences)

    return NarrativeUnit(
        narrative_unit_id=f"nu_{index:04d}",
        section_id="Section_000",
        section_title=section_title,
        paragraph_index=paragraph_index,
        paragraph_index_0based=paragraph_index,
        paragraph_number_1based=paragraph_index + 1,
        citation_ids=[citation.citation_id for citation in citations],
        panel_ids=panel_ids,
        supporting_paragraph=supporting_paragraph,
        supporting_sentences=supporting_sentences,
        inferred_topic=inferred_topic,
        confidence=confidence,
        needs_manual_review=needs_review,
    )


def assign_section_ids(units: list[NarrativeUnit]) -> None:
    section_ids: dict[str, str] = {}
    for unit in units:
        if unit.section_title not in section_ids:
            section_ids[unit.section_title] = f"Section_{len(section_ids) + 1:03d}"
        unit.section_id = section_ids[unit.section_title]


def paragraph_at_index(paragraphs: list[str], index: int) -> str:
    if 0 <= index < len(paragraphs):
        return " ".join(paragraphs[index].split())
    return ""


def compact_supporting_paragraph(source_paragraph: str, supporting_sentences: list[str]) -> str:
    return " ".join(supporting_sentences)


def build_evidence_units(
    narrative_units: list[NarrativeUnit],
    citations: list[CitationGroup],
    panels: dict[str, Panel],
    figure_legends: dict[str, str],
) -> list[EvidenceUnit]:
    citation_by_id = {citation.citation_id: citation for citation in citations}
    evidence_units: list[EvidenceUnit] = []
    for narrative in narrative_units:
        narrative_citations = [citation_by_id[citation_id] for citation_id in narrative.citation_ids]
        for citation_group, grouping_reason in group_citations_for_evidence(narrative_citations):
            cited_panels = unique_preserving_order(
                [panel_id for citation in citation_group for panel_id in citation.normalized_panels]
            )
            panel_paths = [
                panels[panel_id].output_file
                for panel_id in cited_panels
                if panel_id in panels
            ]
            cited_figures = sorted({panel_figure_id(panel_id) for panel_id in cited_panels})
            matched_legend = "\n\n".join(
                figure_legends[figure_id]
                for figure_id in cited_figures
                if figure_id in figure_legends
            ) or None
            supporting_sentences = unique_preserving_order(
                [citation.sentence for citation in citation_group]
            )
            claims = [extract_biological_claim(sentence) for sentence in supporting_sentences]
            evidence_units.append(
                EvidenceUnit(
                    evidence_id=f"ev_{len(evidence_units) + 1:04d}",
                    section_id=narrative.section_id,
                    narrative_unit_id=narrative.narrative_unit_id,
                    paragraph_index=narrative.paragraph_index,
                    paragraph_index_0based=narrative.paragraph_index_0based,
                    paragraph_number_1based=narrative.paragraph_number_1based,
                    supporting_paragraph=narrative.supporting_paragraph,
                    supporting_sentences=supporting_sentences,
                    cited_panels=cited_panels,
                    panel_image_paths=panel_paths,
                    matched_figure_legend=matched_legend,
                    biological_claim=summarize_claim(claims),
                    experiment_type=summarize_experiment_type(
                        [infer_experiment_type(sentence) for sentence in supporting_sentences]
                    ),
                    grouping_reason=grouping_reason,
                    confidence=lowest_confidence(
                        [
                            citation.confidence if not citation.missing_panels else "medium"
                            for citation in citation_group
                        ]
                    ),
                    citation_ids=[citation.citation_id for citation in citation_group],
                )
            )
    return evidence_units


def group_citations_for_evidence(
    citations: list[CitationGroup],
) -> list[tuple[list[CitationGroup], str]]:
    """Sentence-level baseline: one citation sentence becomes one evidence unit."""
    sentence_groups: list[tuple[list[CitationGroup], str]] = []
    citations_by_sentence: dict[int, list[CitationGroup]] = defaultdict(list)
    for citation in citations:
        citations_by_sentence[citation.sentence_index].append(citation)

    for sentence_index in sorted(citations_by_sentence):
        group = citations_by_sentence[sentence_index]
        panels = panels_from_citations(group)
        reason = grouping_reason_for_sentence_group(group, panels)
        sentence_groups.append((group, reason))
    return sentence_groups


def panels_from_citations(citations: list[CitationGroup]) -> list[str]:
    return unique_preserving_order(
        [panel_id for citation in citations for panel_id in citation.normalized_panels]
    )


def grouping_reason_for_sentence_group(citations: list[CitationGroup], panels: list[str]) -> str:
    if len(panels) == 1:
        return "single_panel"
    if is_paired_measurement(panels, [citation.sentence for citation in citations]):
        return "paired_measurement_and_quantification"
    return "same_sentence"


def is_paired_measurement(panel_ids: list[str], sentences: list[str]) -> bool:
    if len(panel_ids) != 2:
        return False
    if not panels_are_sequential(panel_ids):
        return False
    text = " ".join(sentences).lower()
    paired_words = ["quant", "level", "levels", "immunoblot", "western", "accumulated"]
    return any(word in text for word in paired_words)


def build_slides(
    narrative_units: list[NarrativeUnit],
    evidence_units: list[EvidenceUnit],
) -> list[SlideObject]:
    narrative_by_id = {narrative.narrative_unit_id: narrative for narrative in narrative_units}
    slides: list[SlideObject] = []
    for evidence in evidence_units:
        narrative = narrative_by_id[evidence.narrative_unit_id]
        needs_review = slide_needs_manual_review(evidence=evidence, narrative=narrative)
        slides.append(
            SlideObject(
                slide_id=f"slide_{len(slides) + 1:04d}",
                slide_type="result",
                section_id=evidence.section_id,
                section_title=narrative.section_title,
                slide_subtitle=make_slide_subtitle(
                    narrative=narrative,
                    biological_claim=evidence.biological_claim,
                    experiment_type=evidence.experiment_type,
                ),
                narrative_unit_id=narrative.narrative_unit_id,
                evidence_unit_id=evidence.evidence_id,
                paragraph_index=evidence.paragraph_index,
                paragraph_index_0based=evidence.paragraph_index_0based,
                paragraph_number_1based=evidence.paragraph_number_1based,
                evidence_ids=[evidence.evidence_id],
                panel_ids=evidence.cited_panels,
                panel_image_paths=evidence.panel_image_paths,
                supporting_paragraph=evidence.supporting_paragraph,
                supporting_sentences=evidence.supporting_sentences,
                biological_claim=evidence.biological_claim,
                experiment_type=evidence.experiment_type,
                speaker_notes_placeholder=(
                    "Speaker notes will be drafted in a later phase from the "
                    "supporting paragraph, evidence units, and panel images."
                ),
                layout_type="semantic_panel_evidence",
                grouping_reason=evidence.grouping_reason,
                source_citation_ids=evidence.citation_ids,
                confidence=evidence.confidence,
                needs_manual_review=needs_review,
            )
        )
    return slides


def slide_needs_manual_review(evidence: EvidenceUnit, narrative: NarrativeUnit) -> bool:
    reasons = slide_qc_warnings(evidence=evidence, narrative=narrative)
    return bool(reasons)


def slide_qc_warnings(evidence: EvidenceUnit, narrative: NarrativeUnit) -> list[str]:
    warnings = []
    if evidence.paragraph_index_0based != narrative.paragraph_index_0based:
        warnings.append("panels from different paragraphs are grouped together")
    if evidence.section_id != narrative.section_id:
        warnings.append("panels from different Results headings are grouped together")
    if len(evidence.cited_panels) > 4 and evidence.grouping_reason != "same_sentence":
        warnings.append("slide contains more than 4 panels without a same-sentence citation")
    if not evidence.panel_image_paths:
        warnings.append("raw citation exists but no panel image matched")
    if evidence.confidence == "low":
        warnings.append("confidence is low")
    return warnings


def panels_are_sequential(panel_ids: list[str]) -> bool:
    if not panel_ids:
        return False
    figures = {panel_figure_id(panel_id) for panel_id in panel_ids}
    if len(figures) != 1:
        return False
    letters = [panel_id[-1] for panel_id in unique_preserving_order(panel_ids) if panel_id[-1].isalpha()]
    if len(letters) <= 1:
        return True
    codes = [ord(letter) for letter in letters]
    return max(codes) - min(codes) + 1 == len(set(codes))


def infer_section_title(citations: list[CitationGroup]) -> str:
    explicit = next((citation.result_heading for citation in citations if citation.result_heading), None)
    if explicit:
        return explicit

    first_sentence = citations[0].sentence
    for heading in SECTION_TITLE_PREFIXES:
        if first_sentence.startswith(heading):
            return heading
    heading = heading_from_sentence(first_sentence)
    if heading:
        return heading

    figure_id = primary_figure([panel for citation in citations for panel in citation.normalized_panels])
    figure_titles = {
        "Fig1": "Development of a mouse model enabling reversible autophagy suppression",
        "Fig2": "Reversible changes in the proteome and transcriptome",
        "Fig3": "Reversible neuronal proteostasis and ultrastructural changes",
        "Fig4": "Restoration of neuronal function after autophagy rescue",
    }
    return figure_titles.get(figure_id or "", "Results narrative")


def heading_from_sentence(sentence: str) -> str | None:
    markers = [
        " More than ",
        " To investigate ",
        " Consistent with ",
        " Last, ",
        " Because ",
        " The mice ",
    ]
    for marker in markers:
        if marker in sentence:
            candidate = sentence.split(marker, maxsplit=1)[0].strip()
            if 20 <= len(candidate) <= 120 and not candidate.endswith("."):
                return candidate
    return None


def primary_figure(panel_ids: list[str]) -> str | None:
    if not panel_ids:
        return None
    return panel_figure_id(panel_ids[0])


def panel_figure_id(panel_id: str) -> str:
    match = re.match(r"^(Fig\d+)", panel_id)
    return match.group(1) if match else panel_id


def explicitly_compares_figures(citation: CitationGroup) -> bool:
    return len({panel_figure_id(panel_id) for panel_id in citation.normalized_panels}) > 1


def explicit_comparison_text(sentences: list[str]) -> bool:
    text = " ".join(sentences).lower()
    return any(word in text for word in ["compare", "compared", "consistent with", "similarly"])


def extract_biological_claim(sentence: str) -> str:
    cleaned = re.sub(r"\([^)]*(?:Fig\.|fig\.|Figure)[^)]*\)", "", sentence)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or sentence


def infer_experiment_type(text: str) -> str:
    lowered = text.lower()
    keyword_map = [
        ("proteome", "proteomics"),
        ("protein", "proteomics"),
        ("rna", "transcriptomics"),
        ("transcript", "transcriptomics"),
        ("metabol", "metabolomics"),
        ("immunoblot", "immunoblot"),
        ("western", "immunoblot"),
        ("electron microscopy", "electron microscopy"),
        ("microscopy", "microscopy"),
        ("staining", "histology"),
        ("rotarod", "behavioral assay"),
        ("beam test", "behavioral assay"),
        ("maze", "behavioral assay"),
        ("survival", "survival analysis"),
        ("gene ontology", "pathway analysis"),
        ("gsea", "pathway analysis"),
    ]
    for keyword, experiment_type in keyword_map:
        if keyword in lowered:
            return experiment_type
    return "result evidence"


def summarize_experiment(sentences: list[str]) -> str:
    if not sentences:
        return "Results narrative"
    return shorten(extract_biological_claim(sentences[0]), 90)


def summarize_claim(claims: list[str]) -> str:
    meaningful = [claim for claim in claims if claim]
    if not meaningful:
        return "Claim to be reviewed"
    return shorten(" ".join(meaningful), 280)


def summarize_experiment_type(experiment_types: list[str]) -> str:
    unique = unique_preserving_order(experiment_types)
    if len(unique) == 1:
        return unique[0]
    return "; ".join(unique)


def make_slide_subtitle(
    narrative: NarrativeUnit,
    biological_claim: str,
    experiment_type: str,
) -> str:
    if narrative.inferred_topic and narrative.inferred_topic != "Results narrative":
        return narrative.inferred_topic
    if experiment_type != "result evidence":
        return experiment_type.capitalize()
    return shorten(biological_claim, 80)


def lowest_confidence(confidences: list[str]) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    if not confidences:
        return "low"
    return min(confidences, key=lambda item: rank.get(item, 0))


def unique_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def shorten(text: str, max_length: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3].rstrip() + "..."


def extract_figure_legends(article_text: str) -> dict[str, str]:
    legends = {}
    compact = " ".join(article_text.split())
    for match in re.finditer(r"\bFig\.\s*(\d+)\.\s+", compact):
        figure_id = f"Fig{match.group(1)}"
        start = match.start()
        next_match = re.search(r"\bFig\.\s*\d+\.\s+", compact[match.end() :])
        end = match.end() + next_match.start() if next_match else min(len(compact), start + 1800)
        legends[figure_id] = compact[start:end].strip()
    return legends


def write_model_json(path: Path, models: list[BaseModel]) -> None:
    path.write_text(
        json.dumps([model.model_dump() for model in models], indent=2) + "\n",
        encoding="utf-8",
    )


def write_slide_review_csv(path: Path, slides: list[SlideObject]) -> None:
    fieldnames = [
        "slide_id",
        "section_title",
        "slide_subtitle",
        "paragraph_index",
        "paragraph_number_1based",
        "evidence_unit_id",
        "panel_ids",
        "claim",
        "experiment_type",
        "supporting_sentences",
        "grouping_reason",
        "source_citation_ids",
        "confidence",
        "needs_manual_review",
    ]
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for slide in slides:
            writer.writerow(
                {
                    "slide_id": slide.slide_id,
                    "section_title": slide.section_title,
                    "slide_subtitle": slide.slide_subtitle,
                    "paragraph_index": slide.paragraph_index,
                    "paragraph_number_1based": slide.paragraph_number_1based,
                    "evidence_unit_id": slide.evidence_unit_id,
                    "panel_ids": "; ".join(slide.panel_ids),
                    "claim": slide.biological_claim,
                    "experiment_type": slide.experiment_type,
                    "supporting_sentences": " | ".join(slide.supporting_sentences),
                    "grouping_reason": slide.grouping_reason,
                    "source_citation_ids": "; ".join(slide.source_citation_ids),
                    "confidence": slide.confidence,
                    "needs_manual_review": str(slide.needs_manual_review).lower(),
                }
            )


def write_slide_review_html(path: Path, slides: list[SlideObject]) -> None:
    sections: dict[str, dict[int, list[SlideObject]]] = defaultdict(lambda: defaultdict(list))
    for slide in slides:
        sections[slide.section_title][slide.paragraph_number_1based].append(slide)

    section_blocks = []
    for section_title, paragraphs in sections.items():
        paragraph_blocks = []
        for paragraph_number, paragraph_slides in sorted(paragraphs.items()):
            first_slide = paragraph_slides[0]
            all_sentences = unique_preserving_order(
                [sentence for slide in paragraph_slides for sentence in slide.supporting_sentences]
            )
            sentence_items = "".join(f"<li>{escape(sentence)}</li>" for sentence in all_sentences)
            slide_cards = "".join(
                build_slide_card_html(slide=slide, html_dir=path.parent)
                for slide in paragraph_slides
            )
            paragraph_blocks.append(
                '<section class="paragraph-block">'
                f"<h3>Paragraph {paragraph_number}</h3>"
                f"<p><strong>Narrative Unit:</strong> {escape(first_slide.narrative_unit_id)}</p>"
                f"<p><strong>Supporting paragraph text:</strong> {escape(first_slide.supporting_paragraph)}</p>"
                f"<p><strong>Citation sentences:</strong></p><ul>{sentence_items}</ul>"
                "<h4>Proposed Evidence Units and Slide Objects</h4>"
                f"{slide_cards}"
                "</section>"
            )
        section_blocks.append(
            '<section class="section-block">'
            f"<h2>{escape(section_title)}</h2>"
            f"{''.join(paragraph_blocks)}"
            "</section>"
        )

    path.write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>pdf2jc Slide Object Review</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; background: #f7f8fa; }
    h1 { margin-bottom: 4px; }
    .subtitle { color: #52606d; margin-top: 0; }
    .section-block { background: #eef2f7; border-radius: 8px; padding: 18px; margin: 24px 0; }
    .paragraph-block { background: white; border: 1px solid #d9e2ec; border-radius: 8px; padding: 16px; margin: 16px 0; }
    .slide-card { background: #ffffff; border: 1px solid #d9e2ec; border-left: 6px solid #2680c2; border-radius: 8px; padding: 14px; margin: 14px 0; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05); }
    .needs-review { border-left-color: #c2410c; }
    .ready-review { border-left-color: #047857; }
    .slide-card h2 { margin-top: 0; }
    .thumb-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin: 14px 0; }
    figure { margin: 0; border: 1px solid #d9e2ec; border-radius: 6px; padding: 8px; background: #fff; }
    img { width: 100%; height: 150px; object-fit: contain; display: block; background: #ffffff; }
    figcaption { font-size: 13px; color: #52606d; margin-top: 6px; text-align: center; }
    .warning { color: #9a3412; font-weight: 700; }
    li { margin-bottom: 6px; }
  </style>
</head>
<body>
  <h1>pdf2jc Slide Object Review</h1>
  <p class="subtitle">Review semantic Slide Objects before any PowerPoint rendering.</p>
"""
        + "\n".join(section_blocks)
        + """
</body>
</html>
""",
        encoding="utf-8",
    )


def build_slide_card_html(slide: SlideObject, html_dir: Path) -> str:
    thumbnails = []
    for image_path in slide.panel_image_paths:
        relative_path = html_relative_path(path=Path(image_path), html_dir=html_dir)
        thumbnails.append(
            "<figure>"
            f'<img src="{escape(relative_path)}" alt="{escape(Path(image_path).stem)}">'
            f"<figcaption>{escape(Path(image_path).stem)}</figcaption>"
            "</figure>"
        )
    if not thumbnails:
        thumbnails.append('<p class="warning">No matched panel thumbnails.</p>')

    sentences = "".join(f"<li>{escape(sentence)}</li>" for sentence in slide.supporting_sentences)
    review_class = "needs-review" if slide.needs_manual_review else "ready-review"
    return (
        f'<article class="slide-card {review_class}">'
        f"<h4>{escape(slide.evidence_unit_id)} -> {escape(slide.slide_id)}: "
        f"{escape(slide.slide_subtitle)}</h4>"
        f"<p><strong>Grouping reason:</strong> {escape(slide.grouping_reason)}</p>"
        f"<p><strong>Source citations:</strong> {escape(', '.join(slide.source_citation_ids))}</p>"
        f"<p><strong>Panel IDs:</strong> {escape(', '.join(slide.panel_ids))}</p>"
        f"<p><strong>Supporting sentences:</strong></p><ul>{sentences}</ul>"
        f'<div class="thumb-grid">{"".join(thumbnails)}</div>'
        f"<p><strong>Biological Claim:</strong> {escape(slide.biological_claim)}</p>"
        f"<p><strong>Experiment Type:</strong> {escape(slide.experiment_type)}</p>"
        f"<p><strong>Proposed Slide:</strong> {escape(slide.slide_type)} | "
        f"{escape(slide.layout_type)} | confidence {escape(slide.confidence)} | "
        f"needs manual review {str(slide.needs_manual_review).lower()}</p>"
        "</article>"
    )


def html_relative_path(path: Path, html_dir: Path) -> str:
    if path.is_absolute():
        return str(path)
    try:
        return str(path.relative_to(html_dir))
    except ValueError:
        if path.parts and path.parts[0] == html_dir.name:
            return str(Path(*path.parts[1:]))
        return str(path)
