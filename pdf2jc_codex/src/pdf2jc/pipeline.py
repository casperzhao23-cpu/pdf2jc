"""End-to-end mock pipeline orchestration."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from load_manual_figures import load_manual_figures

from .mock_data import load_mock_article
from .panel_detector import split_manual_figures_into_panels
from .pdf_extractor import extract_pdf_text
from .pptx_writer import write_pptx
from .slide_planner import build_slide_plan
from .validator import write_validation_report


@dataclass(frozen=True)
class PipelineResult:
    article_text_path: Path
    manual_figures_path: Path
    manual_figures_dir: Path
    panel_boxes_path: Path
    figure_panels_path: Path
    figures_dir: Path
    panel_detection_report_path: Path
    slide_plan_path: Path
    pptx_path: Path
    validation_report_path: Path


def run_pipeline(
    pdf_path: Path,
    input_figures_dir: Path,
    expected_panels_path: Path,
    output_dir: Path,
) -> PipelineResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_legacy_auto_figure_outputs(output_dir=output_dir)

    article_text_path = extract_pdf_text(pdf_path=pdf_path, output_dir=output_dir)
    manual_figures_path = load_manual_figures(
        input_figures_dir=input_figures_dir,
        output_dir=output_dir,
    )
    (
        panel_boxes_path,
        figure_panels_path,
        figures_dir,
        panel_detection_report_path,
    ) = split_manual_figures_into_panels(
        manual_figures_path=manual_figures_path,
        expected_panels_path=expected_panels_path,
        output_dir=output_dir,
    )

    parsed_results = parse_results(article_text_path=article_text_path)
    citation_map = map_citations(parsed_results=parsed_results)
    panel_evidence = build_panel_evidence(
        figure_panels_path=figure_panels_path,
        citation_map=citation_map,
    )

    article = load_mock_article()
    slide_plan = generate_slide_plan(article=article, panel_evidence=panel_evidence)

    slide_plan_path = output_dir / "slide_plan.json"
    pptx_path = output_dir / "jc_draft.pptx"
    validation_report_path = output_dir / "validation_report.md"

    slide_plan_path.write_text(
        json.dumps(slide_plan, indent=2) + "\n",
        encoding="utf-8",
    )
    make_pptx(slide_plan=slide_plan, output_path=pptx_path)
    write_validation_report(
        slide_plan=slide_plan,
        article_text_path=article_text_path,
        manual_figures_path=manual_figures_path,
        panel_boxes_path=panel_boxes_path,
        figure_panels_path=figure_panels_path,
        panel_detection_report_path=panel_detection_report_path,
        slide_plan_path=slide_plan_path,
        pptx_path=pptx_path,
        output_path=validation_report_path,
    )

    return PipelineResult(
        article_text_path=article_text_path,
        manual_figures_path=manual_figures_path,
        manual_figures_dir=output_dir / "manual_figures",
        panel_boxes_path=panel_boxes_path,
        figure_panels_path=figure_panels_path,
        figures_dir=figures_dir,
        panel_detection_report_path=panel_detection_report_path,
        slide_plan_path=slide_plan_path,
        pptx_path=pptx_path,
        validation_report_path=validation_report_path,
    )


def parse_results(article_text_path: Path) -> dict:
    return {"article_text_path": str(article_text_path), "status": "placeholder"}


def map_citations(parsed_results: dict) -> dict:
    return {"parsed_results": parsed_results, "status": "placeholder"}


def build_panel_evidence(figure_panels_path: Path, citation_map: dict) -> dict:
    return {
        "figure_panels_path": str(figure_panels_path),
        "citation_map": citation_map,
        "status": "placeholder",
    }


def generate_slide_plan(article: dict, panel_evidence: dict) -> list[dict]:
    return build_slide_plan(article)


def make_pptx(slide_plan: list[dict], output_path: Path) -> None:
    write_pptx(slide_plan=slide_plan, output_path=output_path)


def cleanup_legacy_auto_figure_outputs(output_dir: Path) -> None:
    legacy_expected_panels = output_dir / "expected_panels.json"
    if legacy_expected_panels.exists():
        legacy_expected_panels.unlink()

    legacy_pages_dir = output_dir / "pages"
    if legacy_pages_dir.exists():
        shutil.rmtree(legacy_pages_dir)
