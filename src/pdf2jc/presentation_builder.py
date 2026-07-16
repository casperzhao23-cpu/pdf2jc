"""Phase 6: render semantic Slide Objects into an editable PowerPoint deck."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from html import escape
from pathlib import Path


DEFAULT_THEME = {
    "theme": {
        "font": "Arial",
        "background": "#FFFFFF",
        "title_color": "#222222",
        "text_color": "#333333",
        "secondary_text": "#555555",
        "accent": "#B22222",
        "secondary": "#2F5597",
        "divider": "#DDDDDD",
    },
    "layout": {
        "margin": "0.5in",
        "panel_spacing": "0.15in",
        "text_spacing": 1.2,
    },
    "footer": {
        "show_slide_number": True,
        "show_citation": True,
    },
}

PAPER_SHORT_CITATION = "Eguchi et al., Science, 2026"
PAPER_TITLE_SHORT = "Reversible suppression of autophagy reveals neuronal resilience"
VALID_GROUPING_MODES = {"sentence_grouped", "paragraph_grouped", "reviewed"}
SKILL_DIR = Path(
    "/Users/casperzhao/.codex/plugins/cache/openai-primary-runtime/"
    "presentations/26.630.12135/skills/presentations"
)
PRESENTATION_SKILL_ROOT = Path(
    "/Users/casperzhao/.codex/plugins/cache/openai-primary-runtime/presentations"
)


@dataclass(frozen=True)
class PresentationBuildResult:
    pptx_path: Path
    presentation_json_path: Path
    theme_preview_path: Path
    grouping_mode: str
    total_slides: int
    total_figures: int
    slides_requiring_review: int
    qc_warning_count: int
    qc_warnings: list[str]


def build_presentation(
    output_dir: Path,
    grouping_mode: str,
    theme_path: Path,
) -> PresentationBuildResult:
    if grouping_mode not in VALID_GROUPING_MODES:
        raise ValueError(
            "Unknown grouping mode. Use sentence_grouped, paragraph_grouped, or reviewed."
        )

    slides_path = slide_objects_path(output_dir=output_dir, grouping_mode=grouping_mode)
    if not slides_path.exists():
        if grouping_mode == "paragraph_grouped":
            raise FileNotFoundError(
                f"Missing {slides_path}. Please create output/slides.paragraph_grouped.json "
                "or run with --grouping-mode sentence_grouped."
            )
        raise FileNotFoundError(
            f"Missing {slides_path}. Please run Phase 5 first with "
            "PYTHONPATH=src python -m pdf2jc diagnose-slides."
        )

    theme = load_theme(theme_path)
    slide_objects = read_json(slides_path)
    presentation = build_presentation_object(
        slide_objects=slide_objects,
        grouping_mode=grouping_mode,
        theme=theme,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    presentation_json_path = output_dir / "presentation.json"
    theme_preview_path = output_dir / "theme_preview.html"
    pptx_path = output_dir / "jc_draft.pptx"

    presentation_json_path.write_text(
        json.dumps(presentation, indent=2) + "\n",
        encoding="utf-8",
    )
    write_theme_preview(theme=theme, output_path=theme_preview_path)
    render_pptx_with_artifact_tool(
        presentation_json_path=presentation_json_path,
        pptx_path=pptx_path,
    )

    return PresentationBuildResult(
        pptx_path=pptx_path,
        presentation_json_path=presentation_json_path,
        theme_preview_path=theme_preview_path,
        grouping_mode=grouping_mode,
        total_slides=len(presentation["slides"]),
        total_figures=len(
            {
                panel_id_to_figure(panel_id)
                for slide in presentation["slides"]
                for panel_id in slide["panel_ids"]
            }
        ),
        slides_requiring_review=sum(
            1 for slide in presentation["slides"] if slide["needs_manual_review"]
        ),
        qc_warning_count=len(presentation["qc"]["warnings"]),
        qc_warnings=presentation["qc"]["warnings"],
    )


def slide_objects_path(output_dir: Path, grouping_mode: str) -> Path:
    if grouping_mode == "paragraph_grouped":
        return output_dir / "slides.paragraph_grouped.json"
    if grouping_mode == "reviewed":
        return output_dir / "slides.reviewed.json"
    return output_dir / "slides.json"


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not read {path}. Please check that it is valid JSON.") from exc


def load_theme(theme_path: Path) -> dict:
    if not theme_path.exists():
        return DEFAULT_THEME
    theme = json.loads(json.dumps(DEFAULT_THEME))
    current_section = None
    for raw_line in theme_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            theme.setdefault(current_section, {})
            continue
        if current_section and ":" in line:
            key, value = line.strip().split(":", maxsplit=1)
            theme[current_section][key.strip()] = parse_theme_value(value.strip())
    return theme


def parse_theme_value(value: str):
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def build_presentation_object(
    slide_objects: list[dict],
    grouping_mode: str,
    theme: dict,
) -> dict:
    slides = []
    for index, slide_object in enumerate(slide_objects, start=1):
        display_modules = display_modules_for_slide(slide_object)
        content_modules = make_visible_content_modules(slide_object, display_modules=display_modules)
        experiment_purpose = make_experiment_purpose(slide_object, display_modules=display_modules)
        panel_layout = compute_panel_layout(len(slide_object.get("panel_ids", [])))
        slide_model = {
            "presentation_slide_number": index,
            "source_slide_id": slide_object["slide_id"],
            "slide_type": slide_object.get("slide_type", "result"),
            "section_id": slide_object["section_id"],
            "section_title": shorten_visible_text(slide_object["section_title"], 82) if display_modules["section_title"] else "",
            "experiment_purpose": experiment_purpose,
            "experiment_question": experiment_purpose,
            "content_modules": content_modules,
            # A result slide always retains its mapped panel images.  The
            # Composer controls explanatory modules, not whether evidence is
            # present on the slide.
            "panel_ids": slide_object.get("panel_ids", []),
            "panel_image_paths": [
                str(Path(image_path).resolve())
                for image_path in slide_object.get("panel_image_paths", [])
            ],
            "paper_citation": PAPER_SHORT_CITATION,
            "footer_title": PAPER_TITLE_SHORT if display_modules["footer"] else "",
            "speaker_notes": make_speaker_notes(slide_object, content_modules, experiment_purpose) if display_modules["speaker_notes"] else "",
            "needs_manual_review": slide_object.get("needs_manual_review", False),
            "confidence": slide_object.get("confidence", "unknown"),
            "source_citation_ids": slide_object.get("source_citation_ids", []),
            "evidence_unit_id": slide_object.get("evidence_unit_id", ""),
            "display_modules": display_modules,
            "layout": {
                "layout_type": "result_evidence_redesigned",
                "panel_layout": panel_layout,
            },
        }
        slide_model["qc_warnings"] = qc_warnings_for_slide(slide_model, slide_object)
        slides.append(slide_model)
    warnings = [
        f"slide {slide['presentation_slide_number']}: {warning}"
        for slide in slides
        for warning in slide["qc_warnings"]
    ]
    return {
        "source": "Slide Objects",
        "grouping_mode": grouping_mode,
        "theme": theme,
        "paper": {
            "title_short": PAPER_TITLE_SHORT,
            "journal": "Science",
            "year": "2026",
            "citation": PAPER_SHORT_CITATION,
        },
        "qc": {
            "warning_count": len(warnings),
            "warnings": warnings,
        },
        "slides": slides,
    }


def display_modules_for_slide(slide: dict) -> dict:
    defaults = {
        "section_title": True,
        "experiment_purpose": True,
        "panel_images": True,
        "panel_labels": True,
        "citation_sentences": False,
        "biological_claim": True,
        "experiment_type": False,
        "speaker_notes": True,
        "footer": True,
    }
    raw = slide.get("display_modules")
    if isinstance(raw, dict):
        if "subtitle" in raw and "experiment_purpose" not in raw:
            defaults["experiment_purpose"] = bool(raw["subtitle"])
        for key in defaults:
            if key in raw:
                defaults[key] = bool(raw[key])
    defaults["panel_images"] = True
    return defaults


def compute_panel_layout(panel_count: int) -> dict:
    if panel_count <= 1:
        return {"rows": 1, "columns": 1, "name": "large_centered"}
    if panel_count == 2:
        return {"rows": 1, "columns": 2, "name": "left_right"}
    if panel_count == 3:
        return {"rows": 1, "columns": 3, "name": "balanced_horizontal"}
    if panel_count == 4:
        return {"rows": 2, "columns": 2, "name": "two_by_two"}
    if panel_count == 5:
        return {"rows": 2, "columns": 3, "name": "two_plus_three"}
    columns = math.ceil(math.sqrt(panel_count))
    rows = math.ceil(panel_count / columns)
    return {"rows": rows, "columns": columns, "name": "adaptive_grid"}


def make_experiment_purpose(slide: dict, display_modules: dict | None = None) -> str:
    display_modules = display_modules or display_modules_for_slide(slide)
    if not display_modules["experiment_purpose"]:
        return ""
    if slide.get("grouping_strategy"):
        return shorten_visible_text(slide.get("experiment_purpose") or slide.get("slide_subtitle", ""), 76)
    if slide.get("experiment_purpose"):
        return shorten_visible_text(slide["experiment_purpose"], 76)
    panel_ids = slide.get("panel_ids", [])
    panel_set = set(panel_ids)
    experiment_type = slide.get("experiment_type", "result evidence")
    if panel_set == {"Fig1A"}:
        return ""
    if panel_set == {"Fig1B"}:
        return "Does Atg101 disruption impair neuronal quality control?"
    if panel_set == {"Fig1C"}:
        return "How was Atg101 placed under reversible doxycycline control?"
    if panel_set == {"Fig1D", "Fig1E"}:
        return "Is autophagy suppression reversible after doxycycline restoration?"
    if panel_set == {"Fig1F"}:
        return "Which tissues show Atg101 suppression and p62 accumulation?"
    if panel_set == {"Fig1G"}:
        return "What is the viability impact of turning autophagy off?"
    if "behavioral assay" in experiment_type:
        return "Does restoring autophagy improve neuronal function?"
    if "proteomics" in experiment_type:
        return "How does autophagy suppression reshape the neuronal proteome?"
    if "transcriptomics" in experiment_type:
        return "Which gene-expression programs change with autophagy suppression?"
    if "histology" in experiment_type or "microscopy" in experiment_type:
        return "What cellular pathology accompanies autophagy suppression?"
    if "immunoblot" in experiment_type:
        return "Do biochemical markers validate the reversible response?"
    return ""


def make_experiment_question(slide: dict, display_modules: dict | None = None) -> str:
    return make_experiment_purpose(slide=slide, display_modules=display_modules)


def make_visible_content_modules(slide: dict, display_modules: dict | None = None) -> list[dict]:
    """Return independent, vertically stackable explanatory slide modules."""
    display_modules = display_modules or display_modules_for_slide(slide)
    modules = []
    if display_modules["citation_sentences"]:
        sentences = slide.get("supporting_sentences", [])
        if sentences:
            modules.append({
                "kind": "key_findings",
                "heading": "Selected citation sentences",
                "items": [shorten_visible_text(sentence, 120) for sentence in sentences[:2]],
            })
    if display_modules["biological_claim"]:
        claim = slide.get("biological_claim", "")
        if claim:
            modules.append({"kind": "biological_claim", "heading": "Biological claim", "text": shorten_visible_text(claim, 150)})
    if display_modules["experiment_type"]:
        experiment_type = slide.get("experiment_type", "")
        if experiment_type:
            modules.append({"kind": "experiment_type", "heading": "Experiment type", "text": experiment_type})
    if modules:
        return modules
    if slide.get("grouping_strategy"):
        return []
    return [make_default_finding(slide)]


def make_default_finding(slide: dict) -> dict:
    panel_ids = slide.get("panel_ids", [])
    panel_set = set(panel_ids)
    if panel_set == {"Fig1A"}:
        return {
            "kind": "key_findings",
            "items": [
                "FAST engineering places regulatory elements in the Atg101 locus.",
                "FLP and Cre excision create distinct Atg101 control alleles.",
            ],
        }
    if panel_set == {"Fig1B"}:
        return {
            "kind": "conclusion",
            "text": "Atg101 loss produces neuronal p62 accumulation, consistent with impaired autophagy.",
        }
    if panel_set == {"Fig1C"}:
        return {
            "kind": "conclusion",
            "text": "The tTS design enables doxycycline-dependent control of endogenous Atg101.",
        }
    if panel_set == {"Fig1D", "Fig1E"}:
        return {
            "kind": "key_findings",
            "items": [
                "Doxycycline withdrawal reduces ATG101 in brain tissue.",
                "p62 rises during suppression and normalizes after rescue.",
                "The paired blot and quantification support reversibility.",
            ],
        }
    if panel_set == {"Fig1D"}:
        return {
            "kind": "conclusion",
            "text": "Immunoblotting validates rapid biochemical control of the Atg101 system.",
        }
    if panel_set == {"Fig1F"}:
        return {
            "kind": "conclusion",
            "text": "Systemic profiling shows tissue-selective effects of Atg101 suppression.",
        }
    if panel_set == {"Fig1G"}:
        return {
            "kind": "conclusion",
            "text": "Extended loss of autophagy control reduces model viability.",
        }

    experiment_type = slide.get("experiment_type", "result evidence")
    panels = ", ".join(panel_ids) or "mapped panels"
    claim = paraphrase_claim(slide)
    if len(panel_ids) >= 2:
        return {
            "kind": "key_findings",
            "items": [
                f"{panels} summarize the main {experiment_type} evidence.",
                claim,
            ],
        }
    return {
        "kind": "conclusion",
        "text": claim,
    }


def shorten_visible_text(value: str, max_length: int) -> str:
    """Apply a fixed copy budget to text boxes in the editable PPTX."""
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def paraphrase_claim(slide: dict) -> str:
    experiment_type = slide.get("experiment_type", "result evidence")
    section = slide.get("section_title", "")
    panels = ", ".join(slide.get("panel_ids", []))
    if "proteomics" in experiment_type:
        return "Proteomic measurements support a reversible response to autophagy control."
    if "transcriptomics" in experiment_type:
        return "Transcriptomic profiling identifies regulated neuronal gene programs."
    if "metabolomics" in experiment_type:
        return "Metabolite measurements suggest altered nutrient recycling."
    if "behavioral assay" in experiment_type:
        return "Behavioral testing supports functional recovery after autophagy rescue."
    if "electron microscopy" in experiment_type:
        return "Ultrastructural imaging reveals reversible axonal pathology."
    if "histology" in experiment_type:
        return "Histological measurements support the cellular phenotype."
    if "immunoblot" in experiment_type:
        return "Biochemical validation confirms the regulated molecular response."
    if "survival" in experiment_type:
        return "Survival analysis indicates a physiological consequence of autophagy control."
    if "Restoration" in section:
        return "The evidence supports functional improvement after restoring autophagy."
    return f"{panels or 'The mapped evidence'} supports this experimental result."


def make_speaker_notes(slide: dict, content_modules: list[dict], experiment_question: str) -> str:
    supporting_sentences = slide.get("supporting_sentences", [])
    figure_legend = slide.get("matched_figure_legend") or "No matched figure legend was available."
    citation_ids = ", ".join(slide.get("source_citation_ids", [])) or "none"
    evidence_id = slide.get("evidence_unit_id", "unknown")
    panels = ", ".join(slide.get("panel_ids", [])) or "none"
    finding_text = "; ".join(
        module.get("text") or "; ".join(module.get("items", []))
        for module in content_modules
    ) or "the evidence shown on this slide"
    presenter_explanation = (
        f"Frame the experiment around: {experiment_question or 'the visual evidence on the slide'}. "
        f"Explain what each panel contributes, then summarize the interpretation: {finding_text}"
    )
    return "\n".join(
        [
            f"Evidence unit: {evidence_id}",
            f"Citation IDs: {citation_ids}",
            f"Panel IDs: {panels}",
            "",
            "Supporting sentence(s):",
            *[f"- {sentence}" for sentence in supporting_sentences],
            "",
            "Matched figure legend:",
            figure_legend,
            "",
            "Presenter explanation:",
            presenter_explanation,
        ]
    )


def qc_warnings_for_slide(slide_model: dict, slide_object: dict) -> list[str]:
    warnings = []
    if slide_object.get("grouping_strategy"):
        return warnings
    if display_modules_for_slide(slide_object)["citation_sentences"]:
        return warnings
    visible_texts = [slide_model.get("experiment_question", "")]
    for module in slide_model.get("content_modules", []):
        visible_texts.extend(module.get("items", []))
        if module.get("text"):
            visible_texts.append(module["text"])

    supporting_sentences = [clean_sentence(s) for s in slide_object.get("supporting_sentences", [])]
    visible_compact = [clean_sentence(text) for text in visible_texts if text]

    for text in visible_compact:
        for sentence in supporting_sentences:
            if text and sentence and text == sentence:
                warnings.append("visible slide contains a supporting sentence verbatim")
            elif len(text) > 30 and len(sentence) > 30 and text in sentence:
                warnings.append("visible slide text appears copied from supporting sentence")

    raw_purpose = clean_sentence(slide_object.get("experiment_purpose") or slide_object.get("slide_subtitle", ""))
    if raw_purpose and any(raw_purpose == text for text in visible_compact):
        warnings.append("experiment purpose is copied from supporting sentence")

    if has_duplicate_visible_information(visible_compact):
        warnings.append("duplicate information appears in multiple visible locations")
    return sorted(set(warnings))


def has_duplicate_visible_information(texts: list[str]) -> bool:
    normalized = [re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() for text in texts if text]
    for index, text in enumerate(normalized):
        if len(text) < 24:
            continue
        for other in normalized[index + 1 :]:
            if text == other or text in other or other in text:
                return True
    return False


def clean_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.replace(" .", ".").replace(" ,", ",")
    return cleaned


def shorten(text: str, max_length: int) -> str:
    text = clean_sentence(text)
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def panel_id_to_figure(panel_id: str) -> str:
    match = re.match(r"^(Fig\d+)", panel_id)
    return match.group(1) if match else panel_id


def write_theme_preview(theme: dict, output_path: Path) -> None:
    theme_values = theme["theme"]
    swatches = "".join(
        f'<div class="swatch"><span style="background:{escape(str(value))}"></span>'
        f"<code>{escape(key)}: {escape(str(value))}</code></div>"
        for key, value in theme_values.items()
        if str(value).startswith("#")
    )
    output_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>pdf2jc Theme Preview</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: {theme_values['title_color']}; background: {theme_values['background']}; }}
    h1 {{ font-size: 32px; margin-bottom: 8px; }}
    p {{ font-size: 18px; color: {theme_values['text_color']}; }}
    .accent {{ color: {theme_values['accent']}; font-weight: 700; }}
    .swatch {{ display: flex; align-items: center; gap: 12px; margin: 10px 0; }}
    .swatch span {{ width: 48px; height: 28px; border: 1px solid {theme_values['divider']}; display: inline-block; }}
    code {{ color: {theme_values['secondary_text']}; }}
  </style>
</head>
<body>
  <h1>pdf2jc Theme Preview</h1>
  <p><span class="accent">Accent</span> and typography preview for the editable PowerPoint renderer.</p>
  {swatches}
</body>
</html>
""",
        encoding="utf-8",
    )


def render_pptx_with_artifact_tool(
    presentation_json_path: Path,
    pptx_path: Path,
) -> None:
    skill_dir = resolve_presentation_skill_dir()
    if not skill_dir.exists():
        raise FileNotFoundError(
            "Could not find the bundled presentation renderer. Set "
            "PDF2JC_PRESENTATION_SKILL_DIR to the presentations skill directory."
        )

    node = shutil.which("node")
    if node is None:
        raise FileNotFoundError("Node.js is required to render the editable PowerPoint.")

    with tempfile.TemporaryDirectory(prefix="pdf2jc_presentation_") as tmp:
        tmp_dir = Path(tmp)
        setup_script = skill_dir / "container_tools" / "setup_artifact_tool_workspace.mjs"
        subprocess.run(
            [node, str(setup_script), "--workspace", str(tmp_dir)],
            check=True,
            cwd=tmp_dir,
        )
        renderer_path = tmp_dir / "render_pdf2jc_presentation.mjs"
        renderer_path.write_text(RENDERER_JS, encoding="utf-8")
        subprocess.run(
            [
                node,
                str(renderer_path),
                str(presentation_json_path.resolve()),
                str(pptx_path.resolve()),
            ],
            check=True,
            cwd=tmp_dir,
        )


def resolve_presentation_skill_dir() -> Path:
    configured = os.environ.get("PDF2JC_PRESENTATION_SKILL_DIR")
    if configured:
        return Path(configured)
    if SKILL_DIR.exists():
        return SKILL_DIR
    if PRESENTATION_SKILL_ROOT.exists():
        candidates = sorted(
            PRESENTATION_SKILL_ROOT.glob("*/skills/presentations"),
            key=lambda path: path.parts,
            reverse=True,
        )
        for candidate in candidates:
            if (candidate / "container_tools" / "setup_artifact_tool_workspace.mjs").exists():
                return candidate
    return SKILL_DIR


RENDERER_JS = r'''
import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const [presentationJsonPath, pptxPath] = process.argv.slice(2);
const PX = 96;
const pt = (value) => value * (96 / 72);
const inch = (value) => value * PX;

async function main() {
  const model = JSON.parse(await fs.readFile(presentationJsonPath, "utf8"));
  const theme = model.theme.theme;
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });

  for (const slideModel of model.slides) {
    const slide = presentation.slides.add();
    slide.background.fill = theme.background;
    await addResultSlide(slide, slideModel, model);
  }

  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(pptxPath);
}

function addText(slide, text, x, y, w, h, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    typeface: "Arial",
    color: style.color ?? "#333333",
    fontSize: pt(style.pt ?? 18),
    bold: style.bold ?? false,
    wrap: "square",
    autoFit: "shrinkText",
    lineSpacing: style.lineSpacing ?? 1.05,
    insets: { top: 0, right: 0, bottom: 0, left: 0 },
  };
  return shape;
}

function addRule(slide, x, y, w, color) {
  slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: w, height: 1.2 },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
}

async function addResultSlide(slide, slideModel, model) {
  const theme = model.theme.theme;
  const page = { left: inch(0.5), top: 28, width: 1184, height: 646 };

  addText(slide, slideModel.section_title, page.left, page.top, page.width, 36, {
    pt: 28, bold: true, color: theme.title_color,
  });
  if (slideModel.experiment_question) {
    addText(slide, slideModel.experiment_question, page.left, page.top + 43, page.width, 28, {
      pt: 18, bold: false, color: theme.secondary,
    });
  }
  addRule(slide, page.left, page.top + 82, page.width, theme.divider);

  // Panel images are mandatory evidence on a PDF2JC result slide.
  // Reserve a shared lower zone for whichever explanatory modules the reviewer enabled.
  // The modules themselves split this zone vertically, so none of them replaces another.
  const panelArea = { left: page.left, top: 120, width: page.width, height: 370 };
  await addPanels(slide, slideModel, panelArea, theme);

  addContentModules(slide, slideModel.content_modules || [], page.left, 505, page.width, 126, theme);
  addRule(slide, page.left, 642, page.width, theme.divider);

  if (slideModel.display_modules?.footer !== false) {
    const footer = slideModel.footer_title ? `${slideModel.footer_title} | Science | 2026` : "";
    addText(slide, footer, page.left, 660, 900, 18, { pt: 10, color: theme.secondary_text });
    addText(slide, String(slideModel.presentation_slide_number), 1160, 660, 40, 18, {
      pt: 10, color: theme.secondary_text,
    });
  }

  if (slideModel.speaker_notes) {
    slide.speakerNotes.textFrame.setText(slideModel.speaker_notes);
    slide.speakerNotes.setVisible(true);
  }
}

function addContentModules(slide, modules, x, y, w, h, theme) {
  if (!modules.length) return;
  const weights = modules.map((module) => module.kind === "key_findings" ? 2 : module.kind === "biological_claim" ? 1.65 : 0.85);
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
  let cursor = y;
  modules.forEach((module, index) => {
    const isLast = index === modules.length - 1;
    const moduleHeight = isLast ? y + h - cursor : Math.max(25, Math.round(h * weights[index] / totalWeight));
    addContentModule(slide, module, x, cursor, w, moduleHeight, theme);
    cursor += moduleHeight;
  });
}

function addContentModule(slide, finding, x, y, w, h, theme) {
  if (!finding || h < 18) return;
  if (finding.kind === "key_findings") {
    const heading = slide.shapes.add({
      geometry: "textbox",
      position: { left: x, top: y, width: 240, height: 18 },
      fill: "none",
      line: { style: "solid", fill: "none", width: 0 },
    });
    heading.text = finding.heading || "Key findings";
    heading.text.style = {
      typeface: "Arial",
      fontSize: pt(14),
      bold: true,
      color: theme.secondary,
      insets: { top: 0, right: 0, bottom: 0, left: 0 },
    };
    const paragraphs = (finding.items || []).slice(0, 2).map((bullet) => ({
    bulletCharacter: "•",
    marginLeft: 18,
    indent: -10,
      runs: [{ run: bullet, textStyle: { typeface: "Arial", fontSize: "16pt", color: theme.text_color } }],
  }));
    const box = slide.shapes.add({
    geometry: "textbox",
      position: { left: x, top: y + 22, width: w, height: Math.max(12, h - 22) },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
    box.text = paragraphs;
    box.text.style = {
    typeface: "Arial",
      fontSize: pt(16),
    color: theme.text_color,
    wrap: "square",
    autoFit: "shrinkText",
    lineSpacing: 1.1,
    insets: { top: 0, right: 0, bottom: 0, left: 0 },
  };
    return;
  }
  const hasHeading = Boolean(finding.heading);
  if (hasHeading) {
    addText(slide, finding.heading, x, y, w, 16, {
      pt: 12, color: theme.secondary, bold: true,
    });
  }
  addText(slide, finding.text || "", x, y + (hasHeading ? 17 : 4), w, Math.max(12, h - (hasHeading ? 17 : 4)), {
    pt: finding.kind === "experiment_type" ? 14 : 16, color: theme.text_color, bold: false,
  });
}

async function addPanels(slide, slideModel, area, theme) {
  const panelIds = slideModel.panel_ids || [];
  const imagePaths = slideModel.panel_image_paths || [];
  if (panelIds.length === 0) {
    addText(slide, "No panel image matched for this slide.", area.left, area.top, area.width, 80, {
      pt: 18, color: theme.secondary_text,
    });
    return;
  }

  const layout = slideModel.layout.panel_layout;
  const slots = computeSlots(panelIds.length, layout, area);
  for (let i = 0; i < panelIds.length; i++) {
    const slot = slots[i];
    if (slideModel.display_modules?.panel_labels !== false) {
      addText(slide, panelIds[i], slot.left, slot.top - 22, slot.width, 18, {
        pt: 11, bold: true, color: theme.accent,
      });
    }
    const imagePath = imagePaths[i];
    if (imagePath) {
      const imageBytes = await fs.readFile(imagePath);
      slide.images.add({
        blob: imageBytes,
        contentType: contentTypeForPath(imagePath),
        alt: panelIds[i],
        fit: "contain",
        position: { left: slot.left, top: slot.top, width: slot.width, height: slot.height },
      });
    }
  }
}

function contentTypeForPath(imagePath) {
  const lower = imagePath.toLowerCase();
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  return "image/png";
}

function computeSlots(count, layout, area) {
  let rows = layout.rows;
  let columns = layout.columns;
  if (count === 5) {
    rows = 2; columns = 3;
  }
  const gap = 14;
  const slotW = (area.width - gap * (columns - 1)) / columns;
  const slotH = (area.height - gap * (rows - 1)) / rows;
  const slots = [];
  for (let i = 0; i < count; i++) {
    let row = Math.floor(i / columns);
    let col = i % columns;
    if (count === 5 && i >= 2) {
      row = 1;
      col = i - 2;
    }
    slots.push({
      left: area.left + col * (slotW + gap),
      top: area.top + row * (slotH + gap) + 20,
      width: slotW,
      height: slotH - 26,
    });
  }
  return slots;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
'''
