"""Write a simple validation report for generated mock outputs."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile


def write_validation_report(
    slide_plan: list[dict],
    article_text_path: Path,
    manual_figures_path: Path,
    panel_boxes_path: Path,
    figure_panels_path: Path,
    panel_detection_report_path: Path,
    slide_plan_path: Path,
    pptx_path: Path,
    output_path: Path,
) -> None:
    checks = [
        ("Article text JSON exists", article_text_path.exists()),
        ("Manual figures metadata exists", manual_figures_path.exists()),
        ("Panel detection boxes JSON exists", panel_boxes_path.exists()),
        ("Figure panels JSON exists", figure_panels_path.exists()),
        ("Panel detection report exists", panel_detection_report_path.exists()),
        ("Slide plan JSON exists", slide_plan_path.exists()),
        ("PowerPoint file exists", pptx_path.exists()),
        ("Slide plan has at least one slide", len(slide_plan) > 0),
        ("Every slide has a title", all(bool(slide.get("title")) for slide in slide_plan)),
        ("Every slide has bullets", all(bool(slide.get("bullets")) for slide in slide_plan)),
        ("PowerPoint is a readable .pptx zip file", _is_readable_pptx(pptx_path)),
    ]

    lines = [
        "# pdf2jc Validation Report",
        "",
        "This report checks the mock end-to-end skeleton run.",
        "",
        "## Checks",
        "",
    ]

    for label, passed in checks:
        status = "PASS" if passed else "FAIL"
        lines.append(f"- {status}: {label}")

    lines.extend(
        [
            "",
            "## Output Summary",
            "",
            f"- Slide count: {len(slide_plan)}",
            f"- Article text: `{article_text_path}`",
            f"- Manual figures: `{manual_figures_path}`",
            f"- Panel boxes: `{panel_boxes_path}`",
            f"- Figure panels: `{figure_panels_path}`",
            f"- Panel detection report: `{panel_detection_report_path}`",
            f"- Slide plan: `{slide_plan_path}`",
            f"- PowerPoint: `{pptx_path}`",
            "",
            "## Notes",
            "",
            "PDF text extraction is implemented with PyMuPDF.",
            "Figure panel detection uses manually saved figure images from input/figs/.",
            "The PowerPoint draft still uses mock journal club content.",
        ]
    )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _is_readable_pptx(path: Path) -> bool:
    if not path.exists():
        return False

    try:
        with ZipFile(path) as pptx:
            names = set(pptx.namelist())
            return {
                "[Content_Types].xml",
                "ppt/presentation.xml",
            }.issubset(names)
    except Exception:
        return False
