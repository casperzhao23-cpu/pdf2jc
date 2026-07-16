"""Local web interface for running and reviewing the PDF2JC pipeline.

The web layer is intentionally thin: it stores uploaded inputs, calls the same
pipeline stage functions used by the CLI, and serves the existing review
artifacts. It does not reimplement PDF extraction, panel detection, citation
mapping, narrative/evidence construction, slide-object planning, or PowerPoint
rendering.
"""

from __future__ import annotations

import cgi
import importlib.util
import json
import mimetypes
import shutil
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PIPELINE_STEPS = [
    {
        "id": "pdf_text_extraction",
        "label": "PDF text extraction",
        "description": "Extract article text from the uploaded paper PDF.",
    },
    {
        "id": "manual_figure_upload",
        "label": "Manual figure upload",
        "description": "Standardize complete figure images manually uploaded by the user.",
    },
    {
        "id": "panel_detection",
        "label": "Panel detection",
        "description": "Detect panel labels and crop each panel from the complete figures.",
    },
    {
        "id": "citation_mapping",
        "label": "Citation mapping",
        "description": "Map paper citation sentences to available panel images.",
    },
    {
        "id": "narrative_units",
        "label": "Narrative units",
        "description": "Rebuild the Results-section paragraph narrative around citations.",
    },
    {
        "id": "evidence_units",
        "label": "Evidence units",
        "description": "Group evidence only within the same paragraph-level story.",
    },
    {
        "id": "slide_objects",
        "label": "Slide objects",
        "description": "Create reviewable semantic slide objects before PowerPoint rendering.",
    },
    {
        "id": "presentation_builder",
        "label": "Presentation builder",
        "description": "Render editable text and separate panel images into PowerPoint.",
    },
    {
        "id": "editable_powerpoint",
        "label": "Editable journal club PowerPoint",
        "description": "Final downloadable .pptx draft.",
    },
]

ACCEPTED_FIGURE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass(frozen=True)
class WebConfig:
    jobs_dir: Path
    theme_path: Path


def serve(host: str, port: int, jobs_dir: Path, theme_path: Path) -> None:
    """Start the local web server."""
    config = WebConfig(jobs_dir=jobs_dir.resolve(), theme_path=theme_path.resolve())
    config.jobs_dir.mkdir(parents=True, exist_ok=True)

    class PDF2JCRequestHandler(RequestHandler):
        web_config = config

    server = ThreadingHTTPServer((host, port), PDF2JCRequestHandler)
    url = f"http://{host}:{port}"
    print(f"PDF2JC web interface: {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping PDF2JC web interface.")
    finally:
        server.server_close()


class RequestHandler(BaseHTTPRequestHandler):
    web_config: WebConfig

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/app.js", "/styles.css"} or parsed.path.startswith("/api/"):
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_html(INDEX_HTML)
            return
        if path == "/app.js":
            self.send_bytes(APP_JS.encode("utf-8"), "application/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self.send_bytes(STYLES_CSS.encode("utf-8"), "text/css; charset=utf-8")
            return
        if path == "/api/steps":
            self.send_json({"steps": PIPELINE_STEPS})
            return
        if path == "/api/health":
            self.send_json(build_health_payload())
            return
        if path.startswith("/api/jobs/"):
            self.handle_job_api_get(path=path, query=parse_qs(parsed.query))
            return
        if path.startswith("/jobs/"):
            self.handle_job_file(path=path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        path = urlparse(self.path).path
        if path == "/api/jobs":
            self.handle_create_job()
            return
        if path.startswith("/api/jobs/"):
            self.handle_job_api_post(path=path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:
        return

    def handle_create_job(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            self.send_error(HTTPStatus.BAD_REQUEST, "Expected multipart/form-data")
            return

        job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = self.web_config.jobs_dir / job_id
        input_dir = job_dir / "input"
        figures_dir = input_dir / "figs"
        input_dir.mkdir(parents=True, exist_ok=True)
        figures_dir.mkdir(parents=True, exist_ok=True)

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )

        title = field_text(form, "title") or "Untitled PDF2JC run"
        expected_panels = field_text(form, "expected_panels").strip()
        pdf_field = form["pdf"] if "pdf" in form else None
        if pdf_field is None or not getattr(pdf_field, "filename", None):
            shutil.rmtree(job_dir, ignore_errors=True)
            self.send_error(HTTPStatus.BAD_REQUEST, "Please upload a paper PDF.")
            return

        save_upload(pdf_field, input_dir / "paper.pdf")

        figure_fields = form["figures"] if "figures" in form else []
        if not isinstance(figure_fields, list):
            figure_fields = [figure_fields]
        saved_figures = []
        for index, item in enumerate(figure_fields, start=1):
            filename = getattr(item, "filename", "")
            if not filename:
                continue
            suffix = Path(filename).suffix.lower()
            if suffix not in ACCEPTED_FIGURE_EXTENSIONS:
                continue
            target_name = safe_figure_name(filename=filename, fallback_index=index, suffix=suffix)
            target_path = figures_dir / target_name
            save_upload(item, target_path)
            saved_figures.append(target_name)

        if not saved_figures:
            shutil.rmtree(job_dir, ignore_errors=True)
            self.send_error(
                HTTPStatus.BAD_REQUEST,
                "Please upload at least one complete figure image folder or image file.",
            )
            return

        expected_panels_path = input_dir / "expected_panels.json"
        if expected_panels:
            try:
                json.loads(expected_panels)
            except json.JSONDecodeError as exc:
                shutil.rmtree(job_dir, ignore_errors=True)
                self.send_error(HTTPStatus.BAD_REQUEST, f"Expected panel counts must be valid JSON: {exc}")
                return
            expected_panels_path.write_text(expected_panels + "\n", encoding="utf-8")

        write_status(
            job_dir=job_dir,
            payload=initial_status(
                job_id=job_id,
                title=title,
                pdf_name=getattr(pdf_field, "filename", "paper.pdf"),
                figure_names=saved_figures,
            ),
        )
        thread = threading.Thread(
            target=run_job,
            kwargs={
                "job_dir": job_dir,
                "theme_path": self.web_config.theme_path,
            },
            daemon=True,
        )
        thread.start()
        self.send_json({"job_id": job_id, "status_url": f"/api/jobs/{job_id}"}, status=HTTPStatus.CREATED)

    def handle_job_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        job_id = parts[2]
        job_dir = safe_job_dir(self.web_config.jobs_dir, job_id)
        if job_dir is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Job not found")
            return
        if len(parts) == 3:
            self.send_json(read_status(job_dir))
            return
        if len(parts) == 4 and parts[3] == "review-data":
            try:
                self.send_json(build_review_data(job_dir=job_dir))
            except FileNotFoundError as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if len(parts) == 5 and parts[3] == "download" and parts[4] == "pptx":
            self.send_job_artifact(job_dir=job_dir, relative_path=Path("output/jc_draft.pptx"), download=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_job_api_post(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[3] != "slide-composition":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        job_id = parts[2]
        job_dir = safe_job_dir(self.web_config.jobs_dir, job_id)
        if job_dir is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Job not found")
            return
        try:
            payload = self.read_json_body()
            result = save_slide_composition(
                job_dir=job_dir,
                payload=payload,
                theme_path=self.web_config.theme_path,
            )
        except (FileNotFoundError, ValueError) as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:  # pragma: no cover - local interactive path
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self.send_json(result)

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Request body must be valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def handle_job_file(self, path: str) -> None:
        parts = path.strip("/").split("/", maxsplit=3)
        if len(parts) != 4 or parts[2] != "artifact":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        job_id = parts[1]
        relative = Path(unquote(parts[3]))
        job_dir = safe_job_dir(self.web_config.jobs_dir, job_id)
        if job_dir is None:
            self.send_error(HTTPStatus.NOT_FOUND, "Job not found")
            return
        self.send_job_artifact(job_dir=job_dir, relative_path=relative, download=False)

    def send_job_artifact(self, job_dir: Path, relative_path: Path, download: bool) -> None:
        target = (job_dir / relative_path).resolve()
        try:
            target.relative_to(job_dir.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        with target.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status=status)

    def send_html(self, html: str) -> None:
        self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")

    def send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_job(job_dir: Path, theme_path: Path) -> None:
    from load_manual_figures import load_manual_figures

    from .citation_mapper import build_citation_map
    from .narrative_builder import build_slide_objects
    from .panel_detector import split_manual_figures_into_panels
    from .pdf_extractor import extract_pdf_text
    from .presentation_builder import build_presentation

    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    expected_panels_path = input_dir / "expected_panels.json"
    try:
        mark_step(job_dir, "pdf_text_extraction", "running")
        extract_pdf_text(pdf_path=input_dir / "paper.pdf", output_dir=output_dir)
        mark_step(job_dir, "pdf_text_extraction", "complete", artifact="output/article_text.json")

        mark_step(job_dir, "manual_figure_upload", "running")
        manual_figures_path = load_manual_figures(input_figures_dir=input_dir / "figs", output_dir=output_dir)
        mark_step(job_dir, "manual_figure_upload", "complete", artifact="output/manual_figures.json")

        mark_step(job_dir, "panel_detection", "running")
        split_manual_figures_into_panels(
            manual_figures_path=manual_figures_path,
            expected_panels_path=expected_panels_path,
            output_dir=output_dir,
        )
        mark_step(
            job_dir,
            "panel_detection",
            "complete",
            artifact="output/panel_detection_report.md",
            refresh_artifacts=True,
        )

        mark_step(job_dir, "citation_mapping", "running")
        build_citation_map(
            article_text_path=output_dir / "article_text.json",
            figure_panels_path=output_dir / "figure_panels.json",
            output_dir=output_dir,
        )
        mark_step(
            job_dir,
            "citation_mapping",
            "complete",
            artifact="output/citation_qc_table.html",
            refresh_artifacts=True,
        )

        mark_step(job_dir, "narrative_units", "running")
        phase5 = build_slide_objects(
            article_text_path=output_dir / "article_text.json",
            figure_panels_path=output_dir / "figure_panels.json",
            citation_map_path=output_dir / "citation_map.json",
            output_dir=output_dir,
        )
        mark_step(job_dir, "narrative_units", "complete", artifact="output/narrative_units.json")
        mark_step(job_dir, "evidence_units", "complete", artifact="output/evidence_units.json")
        mark_step(
            job_dir,
            "slide_objects",
            "complete",
            artifact=relative_to_job(job_dir, Path(phase5.slide_review_html_path)),
            refresh_artifacts=True,
        )

        mark_step(job_dir, "presentation_builder", "running")
        presentation = build_presentation(
            output_dir=output_dir,
            grouping_mode="sentence_grouped",
            theme_path=theme_path,
        )
        mark_step(
            job_dir,
            "presentation_builder",
            "complete",
            artifact="output/presentation.json",
            refresh_artifacts=True,
        )
        mark_step(
            job_dir,
            "editable_powerpoint",
            "complete",
            artifact=relative_to_job(job_dir, presentation.pptx_path),
            refresh_artifacts=True,
        )
        status = read_status(job_dir)
        status["state"] = "complete"
        status["completed_at"] = now_iso()
        status["summary"] = {
            "total_slides": presentation.total_slides,
            "total_figures": presentation.total_figures,
            "slides_requiring_review": presentation.slides_requiring_review,
            "qc_warning_count": presentation.qc_warning_count,
        }
        status["artifacts"] = discover_artifacts(job_dir)
        write_status(job_dir, status)
    except Exception as exc:  # pragma: no cover - exercised by local runs
        status = read_status(job_dir)
        status["state"] = "failed"
        status["error"] = str(exc)
        status["traceback"] = traceback.format_exc()
        for step in status["steps"]:
            if step["status"] == "running":
                step["status"] = "failed"
                step["message"] = str(exc)
        status["artifacts"] = discover_artifacts(job_dir)
        write_status(job_dir, status)


def build_review_data(job_dir: Path) -> dict:
    output_dir = job_dir / "output"
    panels = read_json_required(output_dir / "figure_panels.json")
    citation_payload = read_json_required(output_dir / "citation_map.json")
    narrative_units = read_json_required(output_dir / "narrative_units.json")
    evidence_units = read_json_required(output_dir / "evidence_units.json")
    auto_slides = read_json_required(output_dir / "slides.json")
    composition_path = output_dir / "slide_composition.json"
    composition = read_json_optional(composition_path, default={"slides": []})

    return {
        "job_id": job_dir.name,
        "panels": [normalize_panel_for_review(job_dir, panel) for panel in panels],
        "citations": [
            normalize_citation_for_review(job_dir, citation)
            for citation in citation_payload.get("citations", [])
        ],
        "narrative_units": narrative_units,
        "evidence_units": [
            normalize_evidence_for_review(job_dir, evidence)
            for evidence in evidence_units
        ],
        "auto_slides": [
            normalize_slide_for_review(job_dir, slide)
            for slide in auto_slides
        ],
        "composition": composition,
        "relationships": build_review_relationships(
            panels=panels,
            citations=citation_payload.get("citations", []),
            narrative_units=narrative_units,
            evidence_units=evidence_units,
        ),
    }


def normalize_panel_for_review(job_dir: Path, panel: dict) -> dict:
    item = dict(panel)
    item["artifact_path"] = path_to_job_artifact(job_dir, panel.get("output_file"))
    item["source_figure_artifact_path"] = path_to_job_artifact(job_dir, panel.get("source_figure"))
    return item


def normalize_citation_for_review(job_dir: Path, citation: dict) -> dict:
    item = dict(citation)
    item["available_panel_artifact_paths"] = [
        path_to_job_artifact(job_dir, path)
        for path in citation.get("available_panel_images", [])
    ]
    return item


def normalize_evidence_for_review(job_dir: Path, evidence: dict) -> dict:
    item = dict(evidence)
    item["panel_artifact_paths"] = [
        path_to_job_artifact(job_dir, path)
        for path in evidence.get("panel_image_paths", [])
    ]
    return item


def normalize_slide_for_review(job_dir: Path, slide: dict) -> dict:
    item = dict(slide)
    item["panel_artifact_paths"] = [
        path_to_job_artifact(job_dir, path)
        for path in slide.get("panel_image_paths", [])
    ]
    return item


def build_review_relationships(
    panels: list[dict],
    citations: list[dict],
    narrative_units: list[dict],
    evidence_units: list[dict],
) -> dict:
    citations_by_panel: dict[str, list[str]] = {}
    evidence_by_panel: dict[str, list[str]] = {}
    evidence_by_narrative: dict[str, list[str]] = {}
    panels_by_figure: dict[str, list[str]] = {}
    for panel in panels:
        panels_by_figure.setdefault(panel.get("figure_id", "Unknown"), []).append(panel.get("panel_id", ""))
    for citation in citations:
        for panel_id in citation.get("normalized_panels", []):
            citations_by_panel.setdefault(panel_id, []).append(citation.get("citation_id", ""))
    for evidence in evidence_units:
        evidence_by_narrative.setdefault(evidence.get("narrative_unit_id", ""), []).append(
            evidence.get("evidence_id", "")
        )
        for panel_id in evidence.get("cited_panels", []):
            evidence_by_panel.setdefault(panel_id, []).append(evidence.get("evidence_id", ""))
    return {
        "panels_by_figure": panels_by_figure,
        "citations_by_panel": citations_by_panel,
        "evidence_by_panel": evidence_by_panel,
        "evidence_by_narrative": evidence_by_narrative,
        "narrative_order": [item.get("narrative_unit_id", "") for item in narrative_units],
    }


def save_slide_composition(job_dir: Path, payload: dict, theme_path: Path) -> dict:
    from .presentation_builder import build_presentation

    output_dir = job_dir / "output"
    review_data = build_review_data(job_dir=job_dir)
    grouping_strategy = str(payload.get("grouping_strategy") or "sentence")
    display_modules = normalize_display_modules(payload.get("display_modules", {}))
    slides_payload = payload.get("slides")
    if slides_payload is None:
        slides_payload = build_grouped_slide_payloads(
            review_data=review_data,
            grouping_strategy=grouping_strategy,
            display_modules=display_modules,
        )
    if not isinstance(slides_payload, list) or not slides_payload:
        raise ValueError("Slide composition must include at least one slide.")

    reviewed_slides = build_reviewed_slides(review_data=review_data, slides_payload=slides_payload)
    composition = {
        "source": "user_slide_composer",
        "saved_at": now_iso(),
        "grouping_strategy": grouping_strategy,
        "display_modules": display_modules,
        "slides": slides_payload,
    }
    composition_path = output_dir / "slide_composition.json"
    reviewed_slides_path = output_dir / "slides.reviewed.json"
    composition_path.write_text(
        json.dumps(composition, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    reviewed_slides_path.write_text(
        json.dumps(reviewed_slides, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    presentation = build_presentation(
        output_dir=output_dir,
        grouping_mode="reviewed",
        theme_path=theme_path,
    )
    status = read_status(job_dir)
    status["artifacts"] = discover_artifacts(job_dir)
    status["reviewed_summary"] = {
        "reviewed_slide_count": len(reviewed_slides),
        "pptx_path": relative_to_job(job_dir, presentation.pptx_path),
        "qc_warning_count": presentation.qc_warning_count,
    }
    write_status(job_dir, status)
    return {
        "ok": True,
        "grouping_strategy": grouping_strategy,
        "display_modules": display_modules,
        "reviewed_slide_count": len(reviewed_slides),
        "composition_path": relative_to_job(job_dir, composition_path),
        "reviewed_slides_path": relative_to_job(job_dir, reviewed_slides_path),
        "pptx_path": relative_to_job(job_dir, presentation.pptx_path),
        "qc_warning_count": presentation.qc_warning_count,
        "qc_warnings": presentation.qc_warnings,
    }


def normalize_display_modules(raw_modules) -> dict:
    defaults = {
        "section_title": True,
        "experiment_purpose": True,
        # Panels are the evidence on a result slide.  They are intentionally
        # not a user-selectable display module in Slide Composer.
        "panel_images": True,
        "panel_labels": True,
        "citation_sentences": False,
        "biological_claim": True,
        "experiment_type": False,
        "speaker_notes": True,
        "footer": True,
    }
    if isinstance(raw_modules, dict):
        if "subtitle" in raw_modules and "experiment_purpose" not in raw_modules:
            defaults["experiment_purpose"] = bool(raw_modules["subtitle"])
        for key in defaults:
            if key in raw_modules:
                defaults[key] = bool(raw_modules[key])
    defaults["panel_images"] = True
    return defaults


def build_grouped_slide_payloads(
    review_data: dict,
    grouping_strategy: str,
    display_modules: dict,
) -> list[dict]:
    evidence_units = review_data["evidence_units"]
    narrative_by_id = {
        item.get("narrative_unit_id"): item
        for item in review_data.get("narrative_units", [])
    }
    if grouping_strategy == "sentence":
        groups = [[evidence] for evidence in evidence_units]
    elif grouping_strategy == "paragraph":
        groups = paragraph_groups(evidence_units)
    elif grouping_strategy in {"compact", "balanced", "hybrid"}:
        groups = compact_sentence_groups(evidence_units, max_panels=4)
    else:
        raise ValueError("Unknown grouping strategy. Use sentence, compact, or paragraph.")

    slides = []
    for index, group in enumerate(groups, start=1):
        evidence_ids = [item["evidence_id"] for item in group]
        panel_ids = unique_strings(
            [panel_id for item in group for panel_id in item.get("cited_panels", [])]
        )
        citation_ids = unique_strings(
            [citation_id for item in group for citation_id in item.get("citation_ids", [])]
        )
        first = group[0]
        section_title = narrative_by_id.get(first.get("narrative_unit_id"), {}).get(
            "section_title", ""
        )
        claim = first_non_empty([item.get("biological_claim", "") for item in group])
        if len(group) > 1:
            claim = summarize_group_claim([item.get("biological_claim", "") for item in group])
        experiment_type = summarize_group_label([item.get("experiment_type", "") for item in group])
        title = claim or first_non_empty(
            [sentence for item in group for sentence in item.get("supporting_sentences", [])]
        )
        experiment_purpose = summarize_experiment_purpose(
            evidence_items=group,
            claim=claim,
            experiment_type=experiment_type,
            section_title=section_title,
        )
        slides.append(
            {
                "title": title or f"Reviewed slide {index}",
                "experiment_purpose": experiment_purpose,
                "slide_subtitle": experiment_purpose,
                "biological_claim": claim,
                "experiment_type": experiment_type or "result evidence",
                "narrative_unit_id": first.get("narrative_unit_id", ""),
                "section_id": first.get("section_id", ""),
                "section_title": section_title,
                "paragraph_index": first.get("paragraph_index"),
                "paragraph_number_1based": first.get("paragraph_number_1based"),
                "evidence_ids": evidence_ids,
                "panel_ids": panel_ids,
                "citation_ids": citation_ids,
                "layout_type": f"{grouping_strategy}_user_reviewed",
                "display_modules": display_modules,
                "grouping_strategy": grouping_strategy,
            }
        )
    return slides


def paragraph_groups(evidence_units: list[dict]) -> list[list[dict]]:
    grouped: dict[tuple[str, int], list[dict]] = {}
    order: list[tuple[str, int]] = []
    for evidence in evidence_units:
        key = (
            str(evidence.get("narrative_unit_id", "")),
            int(evidence.get("paragraph_index_0based", evidence.get("paragraph_index", 0))),
        )
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(evidence)
    return [grouped[key] for key in order]


def compact_sentence_groups(evidence_units: list[dict], max_panels: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_key = None
    for evidence in evidence_units:
        key = (
            evidence.get("narrative_unit_id"),
            evidence.get("paragraph_index_0based", evidence.get("paragraph_index")),
        )
        candidate = current + [evidence]
        candidate_panels = unique_strings(
            [panel_id for item in candidate for panel_id in item.get("cited_panels", [])]
        )
        if current and (key != current_key or len(candidate_panels) > max_panels):
            groups.append(current)
            current = [evidence]
            current_key = key
        else:
            current = candidate
            current_key = key
    if current:
        groups.append(current)
    return groups


def summarize_group_claim(claims: list[str]) -> str:
    meaningful = [claim for claim in claims if claim]
    if not meaningful:
        return ""
    text = " / ".join(meaningful)
    return text if len(text) <= 220 else text[:217].rstrip() + "..."


def summarize_group_label(values: list[str]) -> str:
    unique = unique_strings(values)
    if not unique:
        return ""
    return "; ".join(unique)


def summarize_experiment_purpose(
    evidence_items: list[dict],
    claim: str,
    experiment_type: str,
    section_title: str = "",
) -> str:
    # Keep this deterministic and compact.  Do not call an LLM here: the
    # experiment purpose is an orientation label, while claims and citations
    # remain available as independently selectable evidence modules.
    existing = first_non_empty([item.get("experiment_purpose", "") for item in evidence_items])
    if section_title and section_title != "Results":
        return shorten_text(f"Investigate: {section_title}", 76)
    target = claim or existing or "the reported result"
    if experiment_type and experiment_type != "result evidence":
        return shorten_text(f"Assess {experiment_type}: {target}", 76)
    return shorten_text(f"Test whether {target}", 76)


def shorten_text(text: str, max_length: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3].rstrip() + "..."


def build_reviewed_slides(review_data: dict, slides_payload: list[dict]) -> list[dict]:
    panels_by_id = {panel["panel_id"]: panel for panel in review_data["panels"]}
    citations_by_id = {citation["citation_id"]: citation for citation in review_data["citations"]}
    evidence_by_id = {evidence["evidence_id"]: evidence for evidence in review_data["evidence_units"]}
    narrative_by_id = {
        narrative["narrative_unit_id"]: narrative
        for narrative in review_data["narrative_units"]
    }
    reviewed_slides = []
    for index, raw_slide in enumerate(slides_payload, start=1):
        if not isinstance(raw_slide, dict):
            raise ValueError("Each composed slide must be a JSON object.")
        reviewed_slides.append(
            build_reviewed_slide(
                index=index,
                raw_slide=raw_slide,
                panels_by_id=panels_by_id,
                citations_by_id=citations_by_id,
                evidence_by_id=evidence_by_id,
                narrative_by_id=narrative_by_id,
            )
        )
    return reviewed_slides


def build_reviewed_slide(
    index: int,
    raw_slide: dict,
    panels_by_id: dict[str, dict],
    citations_by_id: dict[str, dict],
    evidence_by_id: dict[str, dict],
    narrative_by_id: dict[str, dict],
) -> dict:
    panel_ids = valid_ids(raw_slide.get("panel_ids", []), panels_by_id)
    citation_ids = valid_ids(raw_slide.get("citation_ids", []), citations_by_id)
    evidence_ids = valid_ids(raw_slide.get("evidence_ids", []), evidence_by_id)
    narrative_unit_id = str(raw_slide.get("narrative_unit_id") or "")

    for evidence_id in evidence_ids:
        for panel_id in evidence_by_id[evidence_id].get("cited_panels", []):
            if panel_id in panels_by_id and panel_id not in panel_ids:
                panel_ids.append(panel_id)
        for citation_id in evidence_by_id[evidence_id].get("citation_ids", []):
            if citation_id in citations_by_id and citation_id not in citation_ids:
                citation_ids.append(citation_id)

    if not narrative_unit_id:
        narrative_unit_id = infer_narrative_id(evidence_ids, citation_ids, evidence_by_id, narrative_by_id)
    narrative = narrative_by_id.get(narrative_unit_id, {})
    evidence_items = [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
    citation_items = [citations_by_id[citation_id] for citation_id in citation_ids]

    supporting_sentences = unique_strings(
        [sentence for item in evidence_items for sentence in item.get("supporting_sentences", [])]
        + [item.get("sentence", "") for item in citation_items]
    )
    if not supporting_sentences and narrative:
        supporting_sentences = narrative.get("supporting_sentences", [])

    supporting_paragraph = (
        str(raw_slide.get("supporting_paragraph") or "")
        or first_non_empty([item.get("supporting_paragraph", "") for item in evidence_items])
        or narrative.get("supporting_paragraph", "")
    )
    biological_claim = (
        str(raw_slide.get("biological_claim") or "").strip()
        or first_non_empty([item.get("biological_claim", "") for item in evidence_items])
        or first_non_empty(supporting_sentences)
        or "Claim to be reviewed"
    )
    experiment_type = (
        str(raw_slide.get("experiment_type") or "").strip()
        or first_non_empty([item.get("experiment_type", "") for item in evidence_items])
        or "result evidence"
    )
    section_id = (
        str(raw_slide.get("section_id") or "").strip()
        or narrative.get("section_id", "")
        or first_non_empty([item.get("section_id", "") for item in evidence_items])
        or "Section_000"
    )
    section_title = (
        str(raw_slide.get("section_title") or "").strip()
        or narrative.get("section_title", "")
        or "Reviewed slide"
    )
    paragraph_index = int(
        raw_slide.get("paragraph_index")
        if raw_slide.get("paragraph_index") is not None
        else narrative.get("paragraph_index", first_number([item.get("paragraph_index") for item in evidence_items], index - 1))
    )
    paragraph_number = int(
        raw_slide.get("paragraph_number_1based")
        if raw_slide.get("paragraph_number_1based") is not None
        else narrative.get("paragraph_number_1based", paragraph_index + 1)
    )
    confidence = lowest_confidence_web(
        [item.get("confidence", "high") for item in citation_items]
        + [item.get("confidence", "high") for item in evidence_items]
    )
    warnings = composition_warnings(
        panel_ids=panel_ids,
        citation_items=citation_items,
        evidence_items=evidence_items,
    )
    return {
        "slide_id": f"reviewed_slide_{index:04d}",
        "slide_type": str(raw_slide.get("slide_type") or "result"),
        "section_id": section_id,
        "section_title": section_title,
        "experiment_purpose": str(
            raw_slide.get("experiment_purpose")
            or raw_slide.get("slide_subtitle")
            or raw_slide.get("title")
            or summarize_experiment_purpose(
                evidence_items=evidence_items,
                claim=biological_claim,
                experiment_type=experiment_type,
                section_title=section_title,
            )
        ),
        "slide_subtitle": str(
            raw_slide.get("slide_subtitle")
            or raw_slide.get("experiment_purpose")
            or raw_slide.get("title")
            or summarize_experiment_purpose(
                evidence_items=evidence_items,
                claim=biological_claim,
                experiment_type=experiment_type,
                section_title=section_title,
            )
        ),
        "narrative_unit_id": narrative_unit_id or "nu_reviewed",
        "evidence_unit_id": evidence_ids[0] if evidence_ids else f"ev_reviewed_{index:04d}",
        "paragraph_index": paragraph_index,
        "paragraph_index_0based": paragraph_index,
        "paragraph_number_1based": paragraph_number,
        "evidence_ids": evidence_ids,
        "panel_ids": panel_ids,
        "panel_image_paths": [
            panels_by_id[panel_id]["output_file"]
            for panel_id in panel_ids
            if panel_id in panels_by_id
        ],
        "supporting_paragraph": supporting_paragraph,
        "supporting_sentences": supporting_sentences,
        "biological_claim": biological_claim,
        "experiment_type": experiment_type,
        "speaker_notes_placeholder": str(raw_slide.get("speaker_notes") or "Reviewed by user in Slide Composer."),
        "layout_type": str(raw_slide.get("layout_type") or layout_for_panel_count(len(panel_ids))),
        "grouping_reason": "user_composed",
        "source_citation_ids": citation_ids,
        "confidence": confidence,
        "needs_manual_review": bool(warnings or raw_slide.get("needs_manual_review", False)),
        "composition_warnings": warnings,
        "display_modules": normalize_display_modules(raw_slide.get("display_modules", {})),
        "grouping_strategy": str(raw_slide.get("grouping_strategy") or "user_composed"),
    }


def valid_ids(values, lookup: dict[str, dict]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value) in lookup]


def infer_narrative_id(
    evidence_ids: list[str],
    citation_ids: list[str],
    evidence_by_id: dict[str, dict],
    narrative_by_id: dict[str, dict],
) -> str:
    if evidence_ids:
        return str(evidence_by_id[evidence_ids[0]].get("narrative_unit_id", ""))
    for narrative_id, narrative in narrative_by_id.items():
        if any(citation_id in narrative.get("citation_ids", []) for citation_id in citation_ids):
            return narrative_id
    return ""


def composition_warnings(
    panel_ids: list[str],
    citation_items: list[dict],
    evidence_items: list[dict],
) -> list[str]:
    warnings = []
    if not panel_ids:
        warnings.append("no panels selected")
    citation_paragraphs = {item.get("paragraph_index") for item in citation_items}
    evidence_paragraphs = {item.get("paragraph_index") for item in evidence_items}
    all_paragraphs = {item for item in citation_paragraphs | evidence_paragraphs if item is not None}
    if len(all_paragraphs) > 1:
        warnings.append("selected evidence spans multiple paragraphs")
    if len(panel_ids) > 6:
        warnings.append("slide has more than 6 panels")
    return warnings


def layout_for_panel_count(panel_count: int) -> str:
    if panel_count <= 1:
        return "single_panel"
    if panel_count == 2:
        return "two_panel_comparison"
    if panel_count <= 4:
        return "panel_grid"
    return "dense_panel_grid"


def first_non_empty(values) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def first_number(values, default: int) -> int:
    for value in values:
        if isinstance(value, int):
            return value
    return default


def unique_strings(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def lowest_confidence_web(confidences: list[str]) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    values = [item for item in confidences if item]
    if not values:
        return "medium"
    return min(values, key=lambda item: rank.get(item, 1))


def read_json_required(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Missing {path.name}. Run the pipeline first.")
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_optional(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def path_to_job_artifact(job_dir: Path, raw_path) -> str | None:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = (job_dir / path).resolve()
    try:
        return relative_to_job(job_dir, path)
    except ValueError:
        return str(raw_path)


def build_health_payload() -> dict:
    checks = [
        {
            "id": "python",
            "label": "Python 3.10+",
            "ok": sys.version_info >= (3, 10),
            "detail": sys.version.split()[0],
        },
        dependency_check("fitz", "PyMuPDF / fitz", "PDF text extraction"),
        dependency_check("cv2", "OpenCV / cv2", "manual figure loading and panel detection"),
        dependency_check("pydantic", "Pydantic", "narrative, evidence, and slide object models"),
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "note": (
            "The web page can open without every dependency, but a full PDF2JC run "
            "requires all checks to pass."
        ),
    }


def dependency_check(module_name: str, label: str, used_for: str) -> dict:
    found = importlib.util.find_spec(module_name) is not None
    return {
        "id": module_name,
        "label": label,
        "ok": found,
        "detail": "available" if found else f"missing; needed for {used_for}",
    }


def initial_status(job_id: str, title: str, pdf_name: str, figure_names: list[str]) -> dict:
    return {
        "job_id": job_id,
        "title": title,
        "state": "queued",
        "created_at": now_iso(),
        "inputs": {
            "paper_pdf": pdf_name,
            "manual_figures": figure_names,
        },
        "steps": [
            {
                "id": step["id"],
                "label": step["label"],
                "description": step["description"],
                "status": "pending",
                "artifact": None,
                "message": "",
            }
            for step in PIPELINE_STEPS
        ],
        "artifacts": {},
    }


def mark_step(
    job_dir: Path,
    step_id: str,
    status: str,
    artifact: str | None = None,
    refresh_artifacts: bool = False,
) -> None:
    payload = read_status(job_dir)
    payload["state"] = "running" if status == "running" else payload.get("state", "running")
    for step in payload["steps"]:
        if step["id"] == step_id:
            step["status"] = status
            if artifact is not None:
                step["artifact"] = str(artifact)
            if status == "running":
                step["started_at"] = now_iso()
            if status == "complete":
                step["completed_at"] = now_iso()
            break
    if refresh_artifacts:
        payload["artifacts"] = discover_artifacts(job_dir)
    write_status(job_dir, payload)


def discover_artifacts(job_dir: Path) -> dict:
    output_dir = job_dir / "output"
    debug_images = sorted(
        relative_to_job(job_dir, path)
        for path in (output_dir / "debug").glob("*.png")
    )
    panel_images = sorted(
        relative_to_job(job_dir, path)
        for path in (output_dir / "figures").glob("*.png")
    )
    return {
        "panel_debug_images": debug_images,
        "panel_images": panel_images,
        "citation_qc_html": existing_relative(job_dir, output_dir / "citation_qc_table.html"),
        "citation_qc_csv": existing_relative(job_dir, output_dir / "citation_qc_table.csv"),
        "slide_review_html": existing_relative(job_dir, output_dir / "slide_review.html"),
        "slide_review_csv": existing_relative(job_dir, output_dir / "slide_review.csv"),
        "presentation_json": existing_relative(job_dir, output_dir / "presentation.json"),
        "slide_composition": existing_relative(job_dir, output_dir / "slide_composition.json"),
        "reviewed_slides": existing_relative(job_dir, output_dir / "slides.reviewed.json"),
        "pptx": existing_relative(job_dir, output_dir / "jc_draft.pptx"),
        "panel_detection_report": existing_relative(job_dir, output_dir / "panel_detection_report.md"),
        "citation_mapping_report": existing_relative(job_dir, output_dir / "citation_mapping_report.md"),
    }


def read_status(job_dir: Path) -> dict:
    status_path = job_dir / "status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"Missing job status: {status_path}")
    return json.loads(status_path.read_text(encoding="utf-8"))


def write_status(job_dir: Path, payload: dict) -> None:
    status_path = job_dir / "status.json"
    status_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def existing_relative(job_dir: Path, path: Path) -> str | None:
    return relative_to_job(job_dir, path) if path.exists() else None


def relative_to_job(job_dir: Path, path: Path) -> str:
    return str(path.resolve().relative_to(job_dir.resolve()))


def safe_job_dir(jobs_dir: Path, job_id: str) -> Path | None:
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        return None
    job_dir = (jobs_dir / job_id).resolve()
    try:
        job_dir.relative_to(jobs_dir.resolve())
    except ValueError:
        return None
    if not job_dir.exists():
        return None
    return job_dir


def save_upload(field, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("wb") as handle:
        shutil.copyfileobj(field.file, handle)


def field_text(form: cgi.FieldStorage, name: str) -> str:
    if name not in form:
        return ""
    value = form[name]
    if isinstance(value, list):
        value = value[0]
    raw = value.value
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def safe_figure_name(filename: str, fallback_index: int, suffix: str) -> str:
    stem = Path(filename.replace("\\", "/")).name
    stem = Path(stem).stem
    cleaned = "".join(ch for ch in stem if ch.isalnum() or ch in {"_", "-"}).strip("_-")
    if not cleaned:
        cleaned = f"fig{fallback_index}"
    if not cleaned.lower().startswith(("fig", "figure")):
        cleaned = f"fig{fallback_index}"
    return f"{cleaned}{suffix}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PDF2JC Web</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main class="shell">
      <section class="hero">
        <p class="eyebrow">PDF2JC local review workspace</p>
        <h1>Turn a paper and manually saved figures into an editable journal club deck.</h1>
        <p class="lede">
          This site is a graphical entry point for the existing PDF2JC Python pipeline.
          Figures are uploaded manually; panel detection, citation mapping, narrative units,
          evidence units, slide objects, and PowerPoint rendering are handled by the project code.
        </p>
      </section>

      <section class="card">
        <div class="job-header">
          <div>
            <p class="eyebrow">Local readiness</p>
            <h2>Environment check</h2>
            <p id="health-summary" class="message">Checking local PDF2JC runtime…</p>
          </div>
        </div>
        <div id="health-checks" class="health-grid"></div>
      </section>

      <section class="card">
        <h2>Start a run</h2>
        <p class="message">
          Upload the paper PDF plus complete figure images that you saved manually.
          PDF2JC will not auto-extract figures from the PDF.
        </p>
        <form id="job-form">
          <label>
            Run title
            <input name="title" type="text" placeholder="Eguchi et al. journal club" />
          </label>
          <label>
            Paper PDF
            <input name="pdf" type="file" accept="application/pdf,.pdf" required />
          </label>
          <label>
            Complete figure folder, saved manually from the paper
            <input id="figure-folder-input" name="figures" type="file" accept=".png,.jpg,.jpeg,.tif,.tiff,image/*" webkitdirectory directory multiple />
            <small>Select a folder such as <code>figs/</code> containing fig1.png, fig2.png, figure3.jpg, etc.</small>
          </label>
          <label>
            Or choose individual figure images
            <input id="figure-file-input" name="figures" type="file" accept=".png,.jpg,.jpeg,.tif,.tiff,image/*" multiple />
            <small>Use this if your browser does not offer folder selection.</small>
          </label>
          <label>
            Optional expected panel counts JSON
            <textarea name="expected_panels" rows="4" placeholder='{"Fig1": 8, "Fig2": 6}'></textarea>
          </label>
          <button type="submit">Run PDF2JC pipeline</button>
          <p id="form-message" class="message"></p>
        </form>
      </section>

      <section id="job" class="card hidden">
        <div class="job-header">
          <div>
            <p class="eyebrow">Current run</p>
            <h2 id="job-title"></h2>
            <p id="job-state" class="message"></p>
          </div>
          <a id="download-pptx" class="button secondary hidden" href="#">Download PPTX</a>
        </div>
        <ol id="steps" class="steps"></ol>
      </section>

      <section id="review" class="review-grid hidden">
        <article class="card">
          <h2>Panel debug images</h2>
          <p>Inspect label candidates and detected panel regions before trusting downstream citation mapping.</p>
          <div id="debug-images" class="image-grid"></div>
        </article>
        <article class="card">
          <h2>Citation QC</h2>
          <p>Check how paper sentences were mapped to panel images.</p>
          <iframe id="citation-frame" title="Citation QC"></iframe>
        </article>
        <article class="card wide">
          <h2>Slide review</h2>
          <p>Review narrative units, evidence units, and slide objects before the final deck.</p>
          <iframe id="slide-frame" title="Slide review"></iframe>
        </article>
        <article id="composer" class="card wide hidden">
          <div class="job-header">
            <div>
              <p class="eyebrow">Human-in-the-loop slide review</p>
              <h2>Slide Composer</h2>
              <p class="message">
                Let PDF2JC decide slide boundaries from citation structure, then choose which
                content modules should appear on each generated slide.
              </p>
            </div>
            <button id="save-composition" type="button" class="secondary">Save reviewed slides & build PPTX</button>
          </div>
          <p id="composer-message" class="message"></p>
          <div class="composer-layout">
            <aside class="module-library">
              <h3>Slide grouping</h3>
              <label class="option-card">
                <input type="radio" name="grouping_strategy" value="sentence" checked />
                <span>
                  <strong>Sentence-level</strong>
                  <small>One slide per citation sentence. This is the most granular boundary.</small>
                </span>
              </label>
              <label class="option-card">
                <input type="radio" name="grouping_strategy" value="compact" />
                <span>
                  <strong>Compact sentence groups</strong>
                  <small>Combine nearby sentence-level evidence within the same paragraph when the panel count stays manageable.</small>
                </span>
              </label>
              <label class="option-card">
                <input type="radio" name="grouping_strategy" value="paragraph" />
                <span>
                  <strong>Paragraph-level</strong>
                  <small>One slide per Results paragraph / narrative unit. This is the broadest boundary.</small>
                </span>
              </label>

              <h3>Visible content modules</h3>
              <p class="message">Panel images are fixed evidence on every result slide. Choose only the explanatory text modules.</p>
              <div id="module-options" class="module-options">
                <label><input type="checkbox" data-module="section_title" checked /> Results heading / section title</label>
                <label><input type="checkbox" data-module="experiment_purpose" checked /> Experiment purpose</label>
                <label><input type="checkbox" data-module="panel_labels" checked /> Panel labels</label>
                <label><input type="checkbox" data-module="biological_claim" checked /> Biological claim</label>
                <label><input type="checkbox" data-module="citation_sentences" /> Selected citation sentences</label>
                <label><input type="checkbox" data-module="experiment_type" /> Experiment type</label>
                <label><input type="checkbox" data-module="speaker_notes" checked /> Speaker notes</label>
                <label><input type="checkbox" data-module="footer" checked /> Footer / slide number</label>
              </div>
            </aside>
            <section class="slide-workbench">
              <h3>Generated slide preview</h3>
              <p class="message">
                This is a scaled 16:9 canvas using the same title, purpose, panel, finding, and footer
                zones as the exported PowerPoint. Module choices do not change the underlying evidence grouping.
              </p>
              <div id="slide-drafts" class="slide-drafts"></div>
            </section>
          </div>
        </article>
      </section>
    </main>
    <div id="panel-modal" class="panel-modal hidden" role="dialog" aria-modal="true" aria-labelledby="panel-modal-title">
      <button id="panel-modal-backdrop" class="panel-modal-backdrop" type="button" aria-label="Close panel preview"></button>
      <section class="panel-modal-content">
        <div class="panel-modal-header">
          <strong id="panel-modal-title">Panel preview</strong>
          <button id="panel-modal-close" type="button" class="panel-modal-close">Close</button>
        </div>
        <img id="panel-modal-image" alt="Selected panel" />
      </section>
    </div>
    <script src="/app.js"></script>
  </body>
</html>
"""


APP_JS = """
const form = document.querySelector("#job-form");
const message = document.querySelector("#form-message");
const jobSection = document.querySelector("#job");
const reviewSection = document.querySelector("#review");
const stepsList = document.querySelector("#steps");
const titleEl = document.querySelector("#job-title");
const stateEl = document.querySelector("#job-state");
const download = document.querySelector("#download-pptx");
const debugImages = document.querySelector("#debug-images");
const citationFrame = document.querySelector("#citation-frame");
const slideFrame = document.querySelector("#slide-frame");
const healthSummary = document.querySelector("#health-summary");
const healthChecks = document.querySelector("#health-checks");
const figureFolderInput = document.querySelector("#figure-folder-input");
const figureFileInput = document.querySelector("#figure-file-input");
const composer = document.querySelector("#composer");
const composerMessage = document.querySelector("#composer-message");
const saveComposition = document.querySelector("#save-composition");
const slideDrafts = document.querySelector("#slide-drafts");
const moduleOptions = document.querySelector("#module-options");
const panelModal = document.querySelector("#panel-modal");
const panelModalTitle = document.querySelector("#panel-modal-title");
const panelModalImage = document.querySelector("#panel-modal-image");
const panelModalClose = document.querySelector("#panel-modal-close");
const panelModalBackdrop = document.querySelector("#panel-modal-backdrop");

let currentJobId = null;
let pollTimer = null;
let reviewData = null;
let composerLoadedForJob = null;

loadHealth();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const figureCount = figureFolderInput.files.length + figureFileInput.files.length;
  if (figureCount === 0) {
    message.textContent = "Please choose a figure folder, or select one or more complete figure images.";
    return;
  }
  message.textContent = "Uploading inputs and starting the pipeline…";
  const response = await fetch("/api/jobs", {
    method: "POST",
    body: new FormData(form),
  });
  if (!response.ok) {
    message.textContent = await response.text();
    return;
  }
  const payload = await response.json();
  currentJobId = payload.job_id;
  reviewData = null;
  composerLoadedForJob = null;
  jobSection.classList.remove("hidden");
  reviewSection.classList.remove("hidden");
  if (pollTimer) clearInterval(pollTimer);
  await refreshJob();
  pollTimer = setInterval(refreshJob, 1800);
});

async function loadHealth() {
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    healthSummary.textContent = health.ok
      ? "Ready for a full local PDF2JC run."
      : "The page is running, but one or more runtime dependencies are missing.";
    healthChecks.innerHTML = "";
    for (const check of health.checks) {
      const item = document.createElement("div");
      item.className = `health-item ${check.ok ? "ok" : "missing"}`;
      item.innerHTML = `
        <span class="status-dot"></span>
        <div>
          <strong>${check.label}</strong>
          <small>${check.detail}</small>
        </div>
      `;
      healthChecks.appendChild(item);
    }
  } catch (error) {
    healthSummary.textContent = "Could not read the local environment check.";
  }
}

async function refreshJob() {
  if (!currentJobId) return;
  const response = await fetch(`/api/jobs/${currentJobId}`);
  if (!response.ok) return;
  const job = await response.json();
  titleEl.textContent = job.title;
  stateEl.textContent = job.state === "failed" ? `Failed: ${job.error}` : `Status: ${job.state}`;
  renderSteps(job);
  renderArtifacts(job);
  if (job.state === "complete" || job.state === "failed") {
    clearInterval(pollTimer);
  }
  if (job.state === "complete" && composerLoadedForJob !== job.job_id) {
    await loadReviewData(job.job_id);
  }
}

function renderSteps(job) {
  stepsList.innerHTML = "";
  for (const step of job.steps) {
    const item = document.createElement("li");
    item.className = `step ${step.status}`;
    const artifact = step.artifact
      ? `<a href="/jobs/${job.job_id}/artifact/${artifactUrl(step.artifact)}" target="_blank" rel="noreferrer">Open artifact</a>`
      : "";
    item.innerHTML = `
      <span class="status-dot"></span>
      <div>
        <strong>${step.label}</strong>
        <p>${step.description}</p>
        <small>${step.status}${step.message ? " · " + step.message : ""}</small>
        ${artifact}
      </div>
    `;
    stepsList.appendChild(item);
  }
}

function renderArtifacts(job) {
  const artifacts = job.artifacts || {};
  debugImages.innerHTML = "";
  for (const path of artifacts.panel_debug_images || []) {
    const link = document.createElement("a");
    link.href = `/jobs/${job.job_id}/artifact/${artifactUrl(path)}`;
    link.target = "_blank";
    const image = document.createElement("img");
    image.src = link.href;
    image.alt = path.split("/").pop();
    link.appendChild(image);
    debugImages.appendChild(link);
  }
  if (artifacts.citation_qc_html) {
    citationFrame.src = `/jobs/${job.job_id}/artifact/${artifactUrl(artifacts.citation_qc_html)}`;
  }
  if (artifacts.slide_review_html) {
    slideFrame.src = `/jobs/${job.job_id}/artifact/${artifactUrl(artifacts.slide_review_html)}`;
  }
  if (artifacts.pptx) {
    download.href = `/api/jobs/${job.job_id}/download/pptx`;
    download.classList.remove("hidden");
  }
}

document.querySelectorAll('input[name="grouping_strategy"]').forEach(input => {
  input.addEventListener("change", renderSlideDrafts);
});
moduleOptions.addEventListener("change", renderSlideDrafts);
slideDrafts.addEventListener("click", event => {
  const panelButton = event.target.closest(".ppt-panel[data-panel-src]");
  if (!panelButton) return;
  panelModalTitle.textContent = `${panelButton.dataset.panelId} · original panel image`;
  panelModalImage.src = panelButton.dataset.panelSrc;
  panelModalImage.alt = panelButton.dataset.panelId;
  panelModal.classList.remove("hidden");
});
panelModalClose.addEventListener("click", closePanelModal);
panelModalBackdrop.addEventListener("click", closePanelModal);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") closePanelModal();
});

function closePanelModal() {
  panelModal.classList.add("hidden");
  panelModalImage.removeAttribute("src");
}

saveComposition.addEventListener("click", async () => {
  if (!currentJobId || !reviewData) {
    composerMessage.textContent = "Run the pipeline before saving reviewed slide settings.";
    return;
  }
  composerMessage.textContent = "Saving reviewed slides and rebuilding the PowerPoint…";
  const response = await fetch(`/api/jobs/${currentJobId}/slide-composition`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      grouping_strategy: selectedGroupingStrategy(),
      display_modules: selectedDisplayModules()
    })
  });
  if (!response.ok) {
    composerMessage.textContent = await response.text();
    return;
  }
  const result = await response.json();
  composerMessage.textContent = `Saved ${result.reviewed_slide_count} reviewed slide(s). PPTX rebuilt with ${result.qc_warning_count} QC warning(s).`;
  download.href = `/api/jobs/${currentJobId}/download/pptx`;
  download.classList.remove("hidden");
  await refreshJob();
});

async function loadReviewData(jobId) {
  const response = await fetch(`/api/jobs/${jobId}/review-data`);
  if (!response.ok) {
    composerMessage.textContent = "Pipeline finished, but review data is not available yet.";
    return;
  }
  reviewData = await response.json();
  composerLoadedForJob = jobId;
  restoreComposerSettings(reviewData.composition || {});
  composer.classList.remove("hidden");
  renderSlideDrafts();
  composerMessage.textContent = `Loaded ${reviewData.evidence_units.length} sentence-level evidence units. Choose a grouping strategy and visible modules.`;
}

function renderSlideDrafts() {
  if (!reviewData) return;
  const strategy = selectedGroupingStrategy();
  const groups = previewGroups(strategy);
  slideDrafts.innerHTML = "";
  if (!groups.length) {
    slideDrafts.innerHTML = '<p class="message">No evidence-backed slide groups are available yet.</p>';
    return;
  }
  groups.forEach((group, index) => {
    const panels = unique(group.flatMap(item => item.cited_panels || []));
    const citations = unique(group.flatMap(item => item.citation_ids || []));
    const claim = group.length === 1
      ? group[0].biological_claim
      : group.map(item => item.biological_claim).filter(Boolean).join(" / ");
    const sectionTitle = groupSectionTitle(group);
    const purpose = previewExperimentPurpose(group, claim);
    const modules = selectedDisplayModules();
    const contentModules = previewContentModules(group, claim, modules);
    const item = document.createElement("article");
    item.className = "draft-card ppt-draft-card";
    item.innerHTML = `
      <div class="draft-meta">
        <strong>Slide ${index + 1}</strong>
        <p>Boundary: ${strategyLabel(strategy)}</p>
        <p>Evidence: ${group.map(item => item.evidence_id).join(", ")}</p>
        <p>Panels: ${panels.join(", ") || "—"}</p>
        <p>Citations: ${citations.join(", ") || "—"}</p>
      </div>
      <div class="ppt-canvas" aria-label="PowerPoint-style slide preview">
        ${modules.section_title ? `<div class="ppt-section-title">${escapeHtml(shorten(sectionTitle, 82))}</div>` : ""}
        ${modules.experiment_purpose ? `<div class="ppt-purpose">${escapeHtml(purpose)}</div>` : ""}
        <div class="ppt-rule ppt-top-rule"></div>
        <div class="ppt-panels ${panelGridClass(panels.length)}">
          ${panels.map(panelId => previewPanel(panelId, modules)).join("") || '<div class="ppt-empty-panel">No mapped panel image</div>'}
        </div>
        ${contentModules.length ? `<div class="ppt-content-stack">${contentModules.map(module => `<section class="ppt-content-module ${module.kind}">${module.html}</section>`).join("")}</div>` : ""}
        <div class="ppt-rule ppt-footer-rule"></div>
        ${modules.footer ? `<div class="ppt-footer"><span>PDF2JC journal club draft</span><span>${index + 1}</span></div>` : ""}
      </div>
    `;
    slideDrafts.appendChild(item);
  });
  composerMessage.textContent = `${groups.length} slide(s) will be generated using ${strategyLabel(strategy)} grouping.`;
}

function selectedGroupingStrategy() {
  return document.querySelector('input[name="grouping_strategy"]:checked')?.value || "sentence";
}

function selectedDisplayModules() {
  const modules = {};
  document.querySelectorAll("[data-module]").forEach(input => {
    modules[input.dataset.module] = input.checked;
  });
  modules.panel_images = true;
  return modules;
}

function restoreComposerSettings(composition) {
  if (composition.grouping_strategy) {
    const input = document.querySelector(`input[name="grouping_strategy"][value="${composition.grouping_strategy}"]`);
    if (input) input.checked = true;
  }
  if (composition.display_modules) {
    document.querySelectorAll("[data-module]").forEach(input => {
      if (Object.prototype.hasOwnProperty.call(composition.display_modules, input.dataset.module)) {
        input.checked = Boolean(composition.display_modules[input.dataset.module]);
      }
    });
  }
}

function previewExperimentPurpose(group, claim) {
  const sectionTitle = groupSectionTitle(group);
  if (sectionTitle && sectionTitle !== "Results") {
    return shorten(`Investigate: ${sectionTitle}`, 76);
  }
  const experimentType = group.map(item => item.experiment_type).find(Boolean);
  const target = claim || group.map(item => item.experiment_purpose).find(Boolean) || "the reported result";
  const prefix = experimentType && experimentType !== "result evidence"
    ? `Assess ${experimentType}: `
    : "Test whether ";
  return shorten(prefix + target, 76);
}

function groupSectionTitle(group) {
  const direct = group[0]?.section_title;
  if (direct) return direct;
  const narrativeId = group[0]?.narrative_unit_id;
  return (reviewData.narrative_units || []).find(item => item.narrative_unit_id === narrativeId)?.section_title || "Results";
}

function previewContentModules(group, claim, modules) {
  const contentModules = [];
  if (modules.citation_sentences) {
    const sentences = unique(group.flatMap(item => item.supporting_sentences || [])).slice(0, 2);
    if (sentences.length) {
      contentModules.push({
        kind: "citations",
        html: `<strong>Selected citation sentences</strong><ul>${sentences.map(sentence => `<li>${escapeHtml(shorten(sentence, 120))}</li>`).join("")}</ul>`
      });
    }
  }
  if (modules.biological_claim && claim) {
    contentModules.push({ kind: "claim", html: `<strong>Biological claim</strong><p>${escapeHtml(shorten(claim, 150))}</p>` });
  }
  if (modules.experiment_type) {
    const experimentType = group.map(item => item.experiment_type).find(Boolean);
    if (experimentType) contentModules.push({ kind: "experiment-type", html: `<strong>Experiment type</strong><p>${escapeHtml(experimentType)}</p>` });
  }
  return contentModules;
}

function previewPanel(panelId, modules) {
  const panel = (reviewData.panels || []).find(item => item.panel_id === panelId);
  const label = modules.panel_labels ? `<span class="ppt-panel-label">${escapeHtml(panelId)}</span>` : "";
  const imagePath = panel?.artifact_path;
  const imageUrl = imagePath && currentJobId
    ? `/jobs/${currentJobId}/artifact/${artifactUrl(imagePath)}`
    : "";
  const action = imageUrl
    ? `data-panel-id="${escapeHtml(panelId)}" data-panel-src="${escapeHtml(imageUrl)}"`
    : "disabled";
  const hint = imageUrl ? "Click to view original panel" : "Panel image unavailable";
  return `<button type="button" class="ppt-panel" ${action}>
    ${label}
    <span class="ppt-panel-placeholder"><span>Panel image</span><strong>${escapeHtml(panelId)}</strong><small>${hint}</small></span>
  </button>`;
}

function panelGridClass(count) {
  if (count <= 1) return "grid-1";
  if (count === 2) return "grid-2";
  if (count === 3) return "grid-3";
  return "grid-many";
}

function previewGroups(strategy) {
  const evidence = reviewData.evidence_units || [];
  if (strategy === "paragraph") return paragraphPreviewGroups(evidence);
  if (strategy === "compact") return compactPreviewGroups(evidence, 4);
  return evidence.map(item => [item]);
}

function paragraphPreviewGroups(evidence) {
  const groups = [];
  const lookup = new Map();
  for (const item of evidence) {
    const key = `${item.narrative_unit_id || ""}:${item.paragraph_index_0based ?? item.paragraph_index ?? 0}`;
    if (!lookup.has(key)) {
      lookup.set(key, []);
      groups.push(lookup.get(key));
    }
    lookup.get(key).push(item);
  }
  return groups;
}

function compactPreviewGroups(evidence, maxPanels) {
  const groups = [];
  let current = [];
  let currentKey = null;
  for (const item of evidence) {
    const key = `${item.narrative_unit_id || ""}:${item.paragraph_index_0based ?? item.paragraph_index ?? 0}`;
    const candidate = current.concat([item]);
    const panels = unique(candidate.flatMap(unit => unit.cited_panels || []));
    if (current.length && (key !== currentKey || panels.length > maxPanels)) {
      groups.push(current);
      current = [item];
      currentKey = key;
    } else {
      current = candidate;
      currentKey = key;
    }
  }
  if (current.length) groups.push(current);
  return groups;
}

function visibleModuleLabels() {
  const labels = {
    section_title: "section title",
    experiment_purpose: "experiment purpose",
    panel_labels: "panel labels",
    citation_sentences: "citation sentences",
    biological_claim: "biological claim",
    experiment_type: "experiment type",
    speaker_notes: "speaker notes",
    footer: "footer"
  };
  return Object.entries(selectedDisplayModules())
    .filter(([, enabled]) => enabled)
    .map(([key]) => labels[key] || key);
}

function strategyLabel(strategy) {
  return {
    sentence: "sentence-level",
    compact: "compact sentence-group",
    paragraph: "paragraph-level"
  }[strategy] || strategy;
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function shorten(value, maxLength) {
  const text = String(value || "").replace(/\\s+/g, " ").trim();
  return text.length <= maxLength ? text : text.slice(0, maxLength - 3).trimEnd() + "...";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function artifactUrl(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}
"""


STYLES_CSS = """
:root {
  color-scheme: light;
  --ink: #18212f;
  --muted: #637083;
  --line: #dde5ef;
  --panel: #ffffff;
  --bg: #f3f6fb;
  --accent: #2f5597;
  --accent-2: #b22222;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(47, 85, 151, 0.15), transparent 34rem),
    linear-gradient(180deg, #ffffff 0, var(--bg) 28rem);
}
.shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 56px 0; }
.hero { max-width: 880px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 8px; text-transform: uppercase; letter-spacing: .12em; color: var(--accent-2); font-size: 12px; font-weight: 800; }
h1 { font-size: clamp(34px, 6vw, 68px); line-height: .96; margin: 0 0 18px; letter-spacing: -0.06em; }
h2 { margin: 0 0 16px; font-size: 24px; letter-spacing: -0.03em; }
.lede { color: var(--muted); font-size: 18px; line-height: 1.7; max-width: 780px; }
.card {
  background: rgba(255,255,255,.88);
  border: 1px solid var(--line);
  border-radius: 24px;
  box-shadow: 0 20px 60px rgba(24,33,47,.08);
  padding: 24px;
  margin-bottom: 22px;
}
form { display: grid; gap: 16px; }
label { display: grid; gap: 8px; color: var(--muted); font-weight: 700; }
label small { color: var(--muted); font-weight: 500; line-height: 1.45; }
code {
  background: rgba(47, 85, 151, .08);
  color: var(--accent);
  border-radius: 6px;
  padding: 1px 5px;
}
input, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 12px 14px;
  font: inherit;
  color: var(--ink);
  background: #fff;
}
button, .button {
  width: fit-content;
  border: 0;
  border-radius: 999px;
  background: var(--accent);
  color: white;
  font-weight: 800;
  padding: 12px 18px;
  text-decoration: none;
  cursor: pointer;
}
.secondary { background: var(--ink); }
.message { color: var(--muted); margin: 8px 0 0; }
.hidden { display: none !important; }
.job-header { display: flex; justify-content: space-between; gap: 20px; align-items: start; }
.steps { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 16px; }
.health-item {
  display: grid;
  grid-template-columns: 20px 1fr;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
}
.health-item small { display: block; color: var(--muted); margin-top: 4px; }
.health-item.ok .status-dot { background: #16864b; }
.health-item.missing .status-dot { background: #b22222; }
.step {
  display: grid;
  grid-template-columns: 20px 1fr;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
}
.step p { margin: 4px 0; color: var(--muted); }
.step a { display: inline-block; margin-top: 6px; color: var(--accent); font-weight: 800; }
.status-dot { width: 12px; height: 12px; border-radius: 999px; margin-top: 5px; background: #c5cfdc; }
.step.running .status-dot { background: #d99a00; box-shadow: 0 0 0 5px rgba(217,154,0,.14); }
.step.complete .status-dot { background: #16864b; }
.step.failed .status-dot { background: #b22222; }
.review-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.wide { grid-column: 1 / -1; }
.image-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.image-grid img { width: 100%; border-radius: 14px; border: 1px solid var(--line); background: #fff; }
iframe { width: 100%; min-height: 520px; border: 1px solid var(--line); border-radius: 16px; background: #fff; }
.composer-layout { display: grid; grid-template-columns: minmax(320px, 0.9fr) minmax(420px, 1.1fr); gap: 22px; margin-top: 18px; }
.module-library, .slide-workbench { min-width: 0; }
.module-library h3, .slide-workbench h3 { margin: 18px 0 10px; }
.option-card {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
  color: var(--ink);
  cursor: pointer;
  margin-bottom: 10px;
}
.option-card input { width: auto; margin-top: 3px; }
.option-card strong { display: block; }
.option-card small { display: block; color: var(--muted); font-weight: 500; line-height: 1.45; margin-top: 4px; }
.module-options {
  display: grid;
  gap: 9px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
}
.module-options label {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--ink);
  font-weight: 650;
}
.module-options input { width: auto; }
.module-stack { display: grid; gap: 10px; max-height: 420px; overflow: auto; padding-right: 4px; }
.compact-stack { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.module-card {
  display: grid;
  grid-template-columns: 18px 1fr;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
  color: var(--ink);
  cursor: pointer;
}
.module-card input { width: auto; margin-top: 3px; }
.module-card strong { display: block; font-size: 14px; color: var(--ink); }
.module-card small { display: block; margin: 4px 0; }
.module-card em { display: block; color: var(--muted); font-style: normal; font-weight: 500; line-height: 1.45; }
.panel-choice img {
  width: 100%;
  height: 96px;
  object-fit: contain;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  margin-bottom: 8px;
}
.composer-actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 22px; }
.slide-drafts { display: grid; gap: 12px; }
.draft-card {
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fff;
  padding: 14px;
}
.draft-card p { margin: 5px 0 0; color: var(--muted); }
.ppt-draft-card { display: grid; gap: 10px; align-items: start; }
.draft-meta { display: flex; flex-wrap: wrap; gap: 3px 14px; align-items: baseline; font-size: 13px; }
.draft-meta strong { color: var(--ink); }
.draft-meta p { margin: 0; font-size: 12px; }
.ppt-canvas {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  container-type: inline-size;
  overflow: hidden;
  background: #fff;
  border: 1px solid #d8d8d8;
  box-shadow: 0 10px 24px rgba(35, 47, 62, .12);
  color: #222;
  font-family: Arial, sans-serif;
}
.ppt-section-title { position: absolute; left: 4%; top: 3.9%; width: 92%; height: 5%; overflow: hidden; white-space: nowrap; font-size: 2.19cqw; line-height: 1; font-weight: 700; }
.ppt-purpose { position: absolute; left: 4%; top: 9.9%; width: 92%; height: 4%; overflow: hidden; white-space: nowrap; color: #2F5597; font-size: 1.41cqw; line-height: 1.2; }
.ppt-rule { position: absolute; left: 4%; width: 92%; height: 1px; background: #ddd; }
.ppt-top-rule { top: 15.3%; }
.ppt-footer-rule { top: 89.2%; }
.ppt-panels { position: absolute; left: 4%; top: 16.7%; width: 92%; height: 51.4%; display: grid; gap: 1.1%; }
.ppt-panels.grid-1 { grid-template-columns: 1fr; }
.ppt-panels.grid-2 { grid-template-columns: repeat(2, 1fr); }
.ppt-panels.grid-3 { grid-template-columns: repeat(3, 1fr); }
.ppt-panels.grid-many { grid-template-columns: repeat(2, 1fr); grid-template-rows: repeat(2, 1fr); }
.ppt-panel {
  position: relative;
  min-width: 0;
  min-height: 0;
  margin: 0;
  padding: 0;
  display: grid;
  place-items: center;
  border: 1px solid #b9c6d8;
  border-radius: 0;
  background: linear-gradient(135deg, #f7f9fc, #edf2f8);
  color: #2f5597;
  cursor: pointer;
}
.ppt-panel:disabled { cursor: default; opacity: .65; }
.ppt-panel:not(:disabled):hover { border-color: #2f5597; background: #e9f0fb; }
.ppt-panel-placeholder { display: grid; gap: 4%; justify-items: center; text-align: center; }
.ppt-panel-placeholder span { font-size: 1.15cqw; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.ppt-panel-placeholder strong { font-size: 2.2cqw; color: #1f2933; }
.ppt-panel-placeholder small { font-size: .9cqw; color: #52606d; }
.ppt-panel-label { position: absolute; z-index: 1; top: 0; left: 0; color: #B22222; font-size: .86cqw; font-weight: 700; }
.ppt-empty-panel { color: #555; font-size: 1.41cqw; text-align: center; }
.ppt-content-stack { position: absolute; left: 4%; top: 70.5%; width: 92%; height: 17.9%; display: flex; flex-direction: column; overflow: hidden; color: #333; }
.ppt-content-module { min-height: 0; overflow: hidden; font-size: 1.3cqw; line-height: 1.13; }
.ppt-content-module.citations { flex: 2; }
.ppt-content-module.claim { flex: 1.65; }
.ppt-content-module.experiment-type { flex: .85; }
.ppt-content-module strong { display: block; color: #2F5597; font-size: 1.03cqw; margin-bottom: .45%; }
.ppt-content-module p { margin: 0; }
.ppt-content-module ul { margin: 0; padding-left: 3%; }
.ppt-content-module li { margin: 0 0 .35%; }
.ppt-footer { position: absolute; left: 4%; top: 91.5%; width: 92%; display: flex; justify-content: space-between; color: #555; font-size: .78cqw; }
.panel-modal { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center; padding: 28px; }
.panel-modal-backdrop { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; border-radius: 0; background: rgba(15, 23, 42, .62); cursor: default; }
.panel-modal-content { position: relative; z-index: 1; width: min(1000px, 92vw); max-height: 90vh; overflow: auto; border-radius: 16px; background: #fff; padding: 16px; box-shadow: 0 24px 72px rgba(15, 23, 42, .35); }
.panel-modal-header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 12px; }
.panel-modal-close { background: var(--ink); padding: 8px 12px; font-size: 13px; }
.panel-modal-content img { display: block; width: 100%; max-height: 76vh; object-fit: contain; background: #fff; }
@media (max-width: 800px) {
  .review-grid { grid-template-columns: 1fr; }
  .job-header { display: grid; }
  .composer-layout { grid-template-columns: 1fr; }
}
"""
