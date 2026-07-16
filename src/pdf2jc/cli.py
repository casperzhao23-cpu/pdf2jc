"""Command line entry point for pdf2jc."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from load_manual_figures import load_manual_figures

from .panel_detector import split_manual_figures_into_panels


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pdf",
        default="input/paper.pdf",
        help="Path to the source biomedical article PDF. Default: input/paper.pdf",
    )
    parser.add_argument(
        "--figures-dir",
        default="input/figs",
        help="Folder of complete manually saved figure images. Default: input/figs",
    )
    parser.add_argument(
        "--expected-panels",
        default="input/expected_panels.json",
        help="Optional JSON file with expected panel counts. Default: input/expected_panels.json",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Folder where output files will be written. Default: output",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a mock journal club PowerPoint draft from article data."
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        help="Run the full pdf2jc pipeline. This is also the default command.",
    )
    add_common_arguments(run_parser)

    diagnose_parser = subparsers.add_parser(
        "diagnose-panels",
        help="Load manual figures, split panels, and print status for every figure.",
    )
    add_common_arguments(diagnose_parser)

    diagnose_citations_parser = subparsers.add_parser(
        "diagnose-citations",
        help="Map article figure citations to detected panel images.",
    )
    diagnose_citations_parser.add_argument(
        "--output-dir",
        default="output",
        help="Folder containing article_text.json and figure_panels.json. Default: output",
    )

    export_citation_qc_parser = subparsers.add_parser(
        "export-citation-qc",
        help="Regenerate citation QC CSV, HTML, and markdown report.",
    )
    export_citation_qc_parser.add_argument(
        "--output-dir",
        default="output",
        help="Folder containing article_text.json and figure_panels.json. Default: output",
    )

    diagnose_slides_parser = subparsers.add_parser(
        "diagnose-slides",
        help="Build narrative units, evidence units, and semantic slide objects.",
    )
    diagnose_slides_parser.add_argument(
        "--output-dir",
        default="output",
        help="Folder containing article_text.json, figure_panels.json, and citation_map.json. Default: output",
    )

    diagnose_evidence_parser = subparsers.add_parser(
        "diagnose-evidence-units",
        help="Print Section -> Paragraph -> Evidence Units -> Slide Objects.",
    )
    diagnose_evidence_parser.add_argument(
        "--output-dir",
        default="output",
        help="Folder containing article_text.json, figure_panels.json, and citation_map.json. Default: output",
    )

    build_presentation_parser = subparsers.add_parser(
        "build-presentation",
        help="Render Slide Objects into an editable Journal Club PowerPoint.",
    )
    add_presentation_arguments(build_presentation_parser)

    diagnose_presentation_parser = subparsers.add_parser(
        "diagnose-presentation",
        help="Print a summary of the generated presentation object.",
    )
    add_presentation_arguments(diagnose_presentation_parser)

    web_parser = subparsers.add_parser(
        "web",
        help="Start the local PDF2JC web interface.",
    )
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Default: 127.0.0.1")
    web_parser.add_argument("--port", default=8765, type=int, help="Port to bind. Default: 8765")
    web_parser.add_argument(
        "--jobs-dir",
        default="output/web_jobs",
        help="Folder for web-uploaded inputs and outputs. Default: output/web_jobs",
    )
    web_parser.add_argument(
        "--theme",
        default="theme.yaml",
        help="Presentation theme YAML file. Default: theme.yaml",
    )

    add_common_arguments(parser)
    return parser


def add_presentation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Folder containing Slide Objects and presentation outputs. Default: output",
    )
    parser.add_argument(
        "--grouping-mode",
        default="sentence_grouped",
        choices=["sentence_grouped", "paragraph_grouped", "reviewed"],
        help="Slide Object input mode. Default: sentence_grouped",
    )
    parser.add_argument(
        "--theme",
        default="theme.yaml",
        help="Presentation theme YAML file. Default: theme.yaml",
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "diagnose-panels":
        diagnose_panels(args)
        return

    if args.command == "diagnose-citations":
        diagnose_citations(args, parser)
        return

    if args.command == "export-citation-qc":
        export_citation_qc(args, parser)
        return

    if args.command == "diagnose-slides":
        diagnose_slides(args, parser)
        return

    if args.command == "diagnose-evidence-units":
        diagnose_slides(args, parser, evidence_focused=True)
        return

    if args.command == "build-presentation":
        build_presentation_command(args, parser)
        return

    if args.command == "diagnose-presentation":
        diagnose_presentation(args, parser)
        return

    if args.command == "web":
        from .web import serve

        serve(
            host=args.host,
            port=args.port,
            jobs_dir=Path(args.jobs_dir),
            theme_path=Path(args.theme),
        )
        return

    from .pipeline import run_pipeline

    result = run_pipeline(
        pdf_path=Path(args.pdf),
        input_figures_dir=Path(args.figures_dir),
        expected_panels_path=Path(args.expected_panels),
        output_dir=Path(args.output_dir),
    )

    print("pdf2jc mock pipeline complete.")
    print(f"Article text: {result.article_text_path}")
    print(f"Manual figures metadata: {result.manual_figures_path}")
    print(f"Standardized manual figures: {result.manual_figures_dir}")
    print(f"Panel boxes: {result.panel_boxes_path}")
    print(f"Figure panels metadata: {result.figure_panels_path}")
    print(f"Panel crops: {result.figures_dir}")
    print(f"Panel detection report: {result.panel_detection_report_path}")
    print(f"Slide plan: {result.slide_plan_path}")
    print(f"PowerPoint: {result.pptx_path}")
    print(f"Validation report: {result.validation_report_path}")


def diagnose_panels(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manual_figures_path = load_manual_figures(
        input_figures_dir=Path(args.figures_dir),
        output_dir=output_dir,
    )
    panel_boxes_path, _, _, report_path = split_manual_figures_into_panels(
        manual_figures_path=manual_figures_path,
        expected_panels_path=Path(args.expected_panels),
        output_dir=output_dir,
    )

    payload = json.loads(panel_boxes_path.read_text(encoding="utf-8"))
    print("Panel diagnosis")
    print(f"Report: {report_path}")
    print(
        "Figure | Expected | Detected Labels | Final Panels | Method | Confidence | Needs Review"
    )
    print(
        "-------|----------|-----------------|--------------|--------|------------|-------------"
    )
    for result in payload["figures"]:
        expected = (
            "null"
            if result["expected_panel_count"] is None
            else str(result["expected_panel_count"])
        )
        print(
            f"{result['figure_id']} | {expected} | {result['detected_label_count']} | "
            f"{result['final_panel_count']} | {result['detection_method']} | "
            f"{result['confidence']} | {str(result['needs_manual_review']).lower()}"
        )


def diagnose_citations(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    citation_map_path, report_path = build_citation_outputs(args=args, parser=parser)

    payload = json.loads(citation_map_path.read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    print("Citation diagnosis")
    print(f"Citation map: {citation_map_path}")
    print(f"Report: {report_path}")
    print(f"QC table: {output_dir / 'citation_qc_table.csv'}")
    print(f"QC HTML: {output_dir / 'citation_qc_table.html'}")
    print(
        "Citation ID | Section | Sentence Preview | Raw Citation | Normalized Panels | Missing | Confidence"
    )
    print(
        "------------|---------|------------------|--------------|-------------------|---------|-----------"
    )
    for citation in payload["citations"]:
        print(
            f"{citation['citation_id']} | {citation['section']} | "
            f"{preview_text(citation['sentence'])} | "
            f"{citation['raw_citation_text']} | "
            f"{', '.join(citation['normalized_panels']) or '-'} | "
            f"{', '.join(citation['missing_panels']) or '-'} | "
            f"{citation['confidence']}"
        )
    print(f"Open {output_dir / 'citation_qc_table.html'} to visually inspect citation-to-panel mapping.")


def export_citation_qc(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    citation_map_path, report_path = build_citation_outputs(args=args, parser=parser)
    output_dir = Path(args.output_dir)
    print("Citation QC export complete.")
    print(f"Citation map: {citation_map_path}")
    print(f"QC CSV: {output_dir / 'citation_qc_table.csv'}")
    print(f"QC HTML: {output_dir / 'citation_qc_table.html'}")
    print(f"Report: {report_path}")
    print(f"Open {output_dir / 'citation_qc_table.html'} to visually inspect citation-to-panel mapping.")


def build_citation_outputs(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[Path, Path]:
    from .citation_mapper import build_citation_map

    output_dir = Path(args.output_dir)
    try:
        return build_citation_map(
            article_text_path=output_dir / "article_text.json",
            figure_panels_path=output_dir / "figure_panels.json",
            output_dir=output_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


def preview_text(text: str, max_length: int = 90) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3] + "..."


def diagnose_slides(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    evidence_focused: bool = False,
) -> None:
    from .narrative_builder import build_slide_objects

    output_dir = Path(args.output_dir)
    try:
        result = build_slide_objects(
            article_text_path=output_dir / "article_text.json",
            figure_panels_path=output_dir / "figure_panels.json",
            citation_map_path=output_dir / "citation_map.json",
            output_dir=output_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")

    narrative_units = json.loads(Path(result.narrative_units_path).read_text(encoding="utf-8"))
    evidence_units = json.loads(Path(result.evidence_units_path).read_text(encoding="utf-8"))
    slides = json.loads(Path(result.slides_path).read_text(encoding="utf-8"))

    evidence_by_narrative: dict[str, list[dict]] = {}
    for evidence in evidence_units:
        evidence_by_narrative.setdefault(evidence["narrative_unit_id"], []).append(evidence)
    slides_by_narrative: dict[str, list[dict]] = {}
    for slide in slides:
        slides_by_narrative.setdefault(slide["narrative_unit_id"], []).append(slide)

    print("Evidence unit diagnosis" if evidence_focused else "Slide object diagnosis")
    print(f"Narrative units: {result.narrative_units_path}")
    print(f"Evidence units: {result.evidence_units_path}")
    print(f"Slide objects: {result.slides_path}")
    print(f"Slide review HTML: {result.slide_review_html_path}")
    print(f"Slide review CSV: {result.slide_review_csv_path}")
    print("")

    current_section = None
    for narrative in narrative_units:
        if narrative["section_title"] != current_section:
            current_section = narrative["section_title"]
            print(f"Section: {current_section}")
        print(
            f"  Paragraph {narrative['paragraph_number_1based']} | "
            f"Narrative Unit: {narrative['narrative_unit_id']} | "
            f"panels {', '.join(narrative['panel_ids'])}"
        )
        for evidence in evidence_by_narrative.get(narrative["narrative_unit_id"], []):
            print(
                f"    Evidence Unit: {evidence['evidence_id']} | "
                f"{', '.join(evidence['cited_panels'])} | "
                f"{evidence['experiment_type']} | {evidence['grouping_reason']}"
            )
        for slide in slides_by_narrative.get(narrative["narrative_unit_id"], []):
            review = "needs review" if slide["needs_manual_review"] else "review ready"
            print(
                f"    Slide Object: {slide['slide_id']} | "
                f"{slide['evidence_unit_id']} | {', '.join(slide['panel_ids'])} | {review}"
            )

    print("")
    print(f"Open {result.slide_review_html_path} to review the proposed Slide Objects.")


def build_presentation_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    result = run_presentation_builder(args=args, parser=parser)
    print("Presentation build complete.")
    print(f"PowerPoint: {result.pptx_path}")
    print(f"Presentation object: {result.presentation_json_path}")
    print(f"Theme preview: {result.theme_preview_path}")
    print(f"QC warnings: {result.qc_warning_count}")
    for warning in result.qc_warnings[:10]:
        print(f"- {warning}")
    print("All slide text is editable PowerPoint text; panel images are inserted as separate image objects.")


def diagnose_presentation(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    result = run_presentation_builder(args=args, parser=parser)
    from .presentation_builder import load_theme

    theme = load_theme(Path(args.theme))
    print("Presentation Summary")
    print(f"Grouping mode: {result.grouping_mode}")
    print(f"Theme: {Path(args.theme)}")
    print(f"Font: {theme['theme']['font']}")
    print(f"Total slides: {result.total_slides}")
    print(f"Total figures: {result.total_figures}")
    print(f"Slides requiring manual review: {result.slides_requiring_review}")
    print(f"QC warnings: {result.qc_warning_count}")
    for warning in result.qc_warnings[:10]:
        print(f"- {warning}")
    print(f"PowerPoint: {result.pptx_path}")


def run_presentation_builder(args: argparse.Namespace, parser: argparse.ArgumentParser):
    from .presentation_builder import build_presentation

    try:
        return build_presentation(
            output_dir=Path(args.output_dir),
            grouping_mode=args.grouping_mode,
            theme_path=Path(args.theme),
        )
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")
