"""Phase 3: split manually loaded complete figures into panels."""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from pathlib import Path

try:
    import cv2
except ModuleNotFoundError:  # pragma: no cover - exercised only before install
    cv2 = None


FIGURE_ID_PATTERN = re.compile(r"^(?:fig|figure)[_\-\s]*(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ManualFigure:
    figure_id: str
    source_image: Path
    expected_panel_count: int | None


@dataclass(frozen=True)
class PanelLabel:
    label: str
    bbox: list[int]
    confidence: float


@dataclass(frozen=True)
class LabelCandidate:
    figure_id: str
    label: str
    bbox: list[int]
    confidence: float
    filter_score: float
    raw_ocr_confidence: float | None
    accepted: bool
    reason: str
    rejection_reason: str | None


def split_manual_figures_into_panels(
    manual_figures_path: Path,
    expected_panels_path: Path,
    output_dir: Path,
) -> tuple[Path, Path, Path, Path]:
    boxes_path = output_dir / "panel_boxes_auto.json"
    figure_panels_path = output_dir / "figure_panels.json"
    label_candidates_path = output_dir / "label_candidates.json"
    label_scores_path = output_dir / "label_scores.json"
    panel_layout_path = output_dir / "panel_layout.json"
    report_path = output_dir / "panel_detection_report.md"
    figures_dir = output_dir / "figures"
    debug_dir = output_dir / "debug"

    figures_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    for old_crop in figures_dir.glob("Fig*.png"):
        old_crop.unlink()
    for old_debug_image in debug_dir.glob("Fig*_detected*.png"):
        old_debug_image.unlink()
    for old_candidate_image in debug_dir.glob("Fig*_label_candidates.png"):
        old_candidate_image.unlink()
    for old_region_image in debug_dir.glob("Fig*_panel_regions.png"):
        old_region_image.unlink()

    expected_counts = _load_expected_counts(expected_panels_path)
    layout_overrides = _load_panel_layout_overrides(
        expected_panels_path.parent / "panel_layout_overrides.json"
    )
    manual_figures = _load_manual_figure_metadata(
        manual_figures_path=manual_figures_path,
        expected_counts=expected_counts,
    )

    results = []
    label_candidates = []
    panel_layout = {}
    for manual_figure in manual_figures:
        result, candidates, layout_metadata = _process_figure(
            manual_figure=manual_figure,
            figures_dir=figures_dir,
            debug_dir=debug_dir,
            layout_override=layout_overrides.get(manual_figure.figure_id),
        )
        results.append(result)
        label_candidates.extend(candidates)
        if layout_metadata:
            panel_layout[manual_figure.figure_id] = layout_metadata

    boxes_payload = {
        "manual_figures_file": str(manual_figures_path),
        "expected_panels_file": str(expected_panels_path)
        if expected_panels_path.exists()
        else None,
        "figures": results,
    }
    boxes_path.write_text(json.dumps(boxes_payload, indent=2) + "\n", encoding="utf-8")
    label_candidates_path.write_text(
        json.dumps(label_candidates, indent=2) + "\n",
        encoding="utf-8",
    )
    label_scores_path.write_text(
        json.dumps(label_candidates, indent=2) + "\n",
        encoding="utf-8",
    )
    panel_layout_path.write_text(
        json.dumps(panel_layout, indent=2) + "\n",
        encoding="utf-8",
    )

    panel_payload = [
        panel
        for result in results
        for panel in result["panels"]
    ]
    figure_panels_path.write_text(
        json.dumps(panel_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_detection_report(results=results, output_path=report_path)

    return boxes_path, figure_panels_path, figures_dir, report_path


def _load_panel_layout_overrides(override_path: Path) -> dict[str, list[list[str]]]:
    if not override_path.exists():
        return {}
    try:
        payload = json.loads(override_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not read {override_path}. Please check that it is valid JSON. "
            f"Example: {{\"Fig2\": {{\"rows\": [[\"A\", \"B\"], [\"C\", \"D\", \"E\"]]}}}}. "
            f"Details: {exc}"
        ) from exc

    overrides = {}
    for raw_figure_id, raw_layout in payload.items():
        figure_id = _normalize_figure_id(str(raw_figure_id))
        rows = raw_layout.get("rows", raw_layout) if isinstance(raw_layout, dict) else raw_layout
        overrides[figure_id] = [[str(label).upper() for label in row] for row in rows]
    return overrides


def sort_candidate_boxes_spatially(boxes: list[list[int]]) -> list[list[int]]:
    """Sort boxes left-to-right within top-to-bottom rows."""
    if not boxes:
        return []

    sorted_by_y = sorted(boxes, key=lambda box: (box[1], box[0]))
    median_height = sorted(box[3] for box in sorted_by_y)[len(sorted_by_y) // 2]
    row_tolerance = max(12, int(median_height * 0.6))

    rows: list[list[list[int]]] = []
    for box in sorted_by_y:
        y_center = box[1] + box[3] / 2
        placed = False
        for row in rows:
            row_center = sum(existing[1] + existing[3] / 2 for existing in row) / len(row)
            if abs(y_center - row_center) <= row_tolerance:
                row.append(box)
                placed = True
                break
        if not placed:
            rows.append([box])

    rows.sort(key=lambda row: min(box[1] for box in row))
    ordered: list[list[int]] = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda box: box[0]))
    return ordered


def _load_expected_counts(expected_panels_path: Path) -> dict[str, int]:
    if not expected_panels_path.exists():
        return {}

    try:
        payload = json.loads(expected_panels_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not read {expected_panels_path}. Please check that it is valid JSON. "
            f"Example: {{\"Fig1\": 8, \"Fig2\": 6}}. Details: {exc}"
        ) from exc

    expected_counts = {}
    for raw_figure_id, raw_count in payload.items():
        figure_id = _normalize_figure_id(str(raw_figure_id))
        expected_counts[figure_id] = int(raw_count)
    return expected_counts


def _load_manual_figure_metadata(
    manual_figures_path: Path,
    expected_counts: dict[str, int],
) -> list[ManualFigure]:
    try:
        payload = json.loads(manual_figures_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Manual figure metadata not found: {manual_figures_path}. "
            "Run the load_manual_figures step first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not read {manual_figures_path}. Please check that it is valid JSON. "
            f"Details: {exc}"
        ) from exc

    figures = []
    for raw in payload:
        if raw.get("status") != "loaded":
            continue
        figure_id = _normalize_figure_id(str(raw["figure_id"]))
        figures.append(
            ManualFigure(
                figure_id=figure_id,
                source_image=Path(raw["output_path"]),
                expected_panel_count=expected_counts.get(figure_id),
            )
        )

    return figures


def _process_figure(
    manual_figure: ManualFigure,
    figures_dir: Path,
    debug_dir: Path,
    layout_override: list[list[str]] | None,
) -> tuple[dict, list[dict], dict | None]:
    warnings = []
    panels = []
    figure_id = _normalize_figure_id(manual_figure.figure_id)

    if cv2 is None:
        raise RuntimeError(
            "OpenCV is not installed yet. Run: python -m pip install -r requirements.txt"
        )

    source_image = manual_figure.source_image
    if not source_image.exists():
        warnings.append(
            f"Source image not found: {source_image}. Check output/manual_figures.json."
        )
        return (
            _figure_result(
                figure_id=figure_id,
                source_image=source_image,
                expected_panel_count=manual_figure.expected_panel_count,
                detected_panel_count=0,
                warnings=warnings,
                panels=[],
                debug_image=None,
                detection_method="none",
                confidence="low",
                detected_labels=[],
            ),
            [],
            None,
        )

    image = cv2.imread(str(source_image))
    if image is None:
        warnings.append(
            f"Could not open image: {source_image}. Try saving the figure as PNG or JPG."
        )
        return (
            _figure_result(
                figure_id=figure_id,
                source_image=source_image,
                expected_panel_count=manual_figure.expected_panel_count,
                detected_panel_count=0,
                warnings=warnings,
                panels=[],
                debug_image=None,
                detection_method="none",
                confidence="low",
                detected_labels=[],
            ),
            [],
            None,
        )

    labels, candidates = _detect_panel_labels(image=image, figure_id=figure_id)
    method = "label_anchor"
    confidence = "high"
    debug_labels_image = debug_dir / f"{figure_id}_detected_labels.png"
    debug_candidates_image = debug_dir / f"{figure_id}_label_candidates.png"
    debug_regions_image = debug_dir / f"{figure_id}_panel_regions.png"
    debug_row_clusters_image = debug_dir / f"{figure_id}_row_clusters.png"
    debug_boxes_image = debug_dir / f"{figure_id}_detected_boxes.png"
    layout_metadata = None

    if len(labels) >= 2:
        boxes, layout_metadata = _create_panel_regions_from_labels(
            image=image,
            labels=labels,
            layout_override=layout_override,
        )
    else:
        method = "contour_fallback"
        confidence = "low"
        boxes = _detect_candidate_boxes(image)
        if labels:
            warnings.append(
                "Large uppercase panel labels were found, but the label count did not match "
                "input/expected_panels.json. Used contour fallback and marked for manual review."
            )
        else:
            warnings.append(
                "Could not find large uppercase panel labels. Used the older contour-based "
                "fallback, which may split internal figure components. Manual review is needed."
            )
        if not boxes:
            warnings.append(
                "No panel boxes were detected. This figure needs manual review or tuned thresholds."
            )

    _write_label_debug_image(image=image, labels=labels, output_file=debug_labels_image)
    _write_candidate_debug_image(
        image=image,
        candidates=candidates,
        output_file=debug_candidates_image,
    )
    _write_region_debug_image(image=image, panels=boxes, output_file=debug_regions_image)
    _write_row_cluster_debug_image(
        image=image,
        labels=labels,
        panels=boxes,
        layout_metadata=layout_metadata,
        output_file=debug_row_clusters_image,
    )
    if method == "contour_fallback":
        _write_debug_image(image=image, boxes=boxes, output_file=debug_boxes_image)
    debug_image = debug_regions_image

    for index, bbox in enumerate(boxes):
        panel_id = f"{figure_id}{string.ascii_uppercase[index]}"
        output_file = figures_dir / f"{panel_id}.png"
        _crop_panel(image=image, bbox=bbox, output_file=output_file)
        panels.append(
            {
                "figure_id": figure_id,
                "panel_id": panel_id,
                "bbox": bbox,
                "source_figure": str(source_image),
                "output_file": str(output_file),
                "detection_method": method,
                "confidence": confidence,
            }
        )

    return (
        _figure_result(
            figure_id=figure_id,
            source_image=source_image,
            expected_panel_count=manual_figure.expected_panel_count,
            detected_panel_count=len(boxes),
            warnings=warnings,
            panels=panels,
            debug_image=debug_image,
            detection_method=method,
            confidence=confidence,
            detected_labels=labels,
            rejected_candidates=[
                candidate for candidate in candidates if not candidate.accepted
            ],
        ),
        [_candidate_to_dict(candidate) for candidate in candidates],
        layout_metadata,
    )


def _detect_panel_labels(image, figure_id: str) -> tuple[list[PanelLabel], list[LabelCandidate]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, threshold = cv2.threshold(gray, 125, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    image_height, image_width = image.shape[:2]
    min_height = max(40, int(image_height * 0.012))
    max_height = max(min_height + 1, int(image_height * 0.04))
    min_width = max(8, int(image_width * 0.002))
    max_width = max(min_width + 1, int(image_width * 0.025))

    raw_candidates = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if not (min_width <= width <= max_width and min_height <= height <= max_height):
            continue
        if area < 300:
            continue
        raw_candidates.append(
            _score_label_candidate(
                image=image,
                threshold=threshold,
                contour=contour,
                bbox=[int(x), int(y), int(width), int(height)],
                raw_boxes=[],
            )
        )

    raw_boxes = [candidate["bbox"] for candidate in raw_candidates]
    scored_candidates = []
    for raw_candidate in raw_candidates:
        scored_candidates.append(
            _score_label_candidate(
                image=image,
                threshold=threshold,
                contour=raw_candidate["contour"],
                bbox=raw_candidate["bbox"],
                raw_boxes=raw_boxes,
            )
        )

    deduped = _apply_spatial_label_cleanup(
        _dedupe_label_candidates(scored_candidates),
        image_width=image_width,
        image_height=image_height,
    )
    candidates = [
        LabelCandidate(
            figure_id=figure_id,
            label=_guess_candidate_label(index),
            bbox=candidate["bbox"],
            confidence=candidate["confidence"],
            filter_score=candidate["filter_score"],
            raw_ocr_confidence=candidate["raw_ocr_confidence"],
            accepted=candidate["accepted"],
            reason=candidate["reason"],
            rejection_reason=candidate["rejection_reason"],
        )
        for index, candidate in enumerate(
            sort_label_candidate_dicts_spatially(deduped)
        )
    ]

    accepted_boxes = [candidate.bbox for candidate in candidates if candidate.accepted]
    sorted_boxes = sort_candidate_boxes_spatially(accepted_boxes)
    confidence_by_box = {tuple(candidate.bbox): candidate.confidence for candidate in candidates}

    labels = []
    for index, bbox in enumerate(sorted_boxes):
        if index >= len(string.ascii_uppercase):
            break
        labels.append(
            PanelLabel(
                label=string.ascii_uppercase[index],
                bbox=bbox,
                confidence=confidence_by_box.get(tuple(bbox), 0.8),
            )
        )
    accepted_by_box = {tuple(label.bbox): label.label for label in labels}
    relabeled_candidates = []
    for candidate in candidates:
        if candidate.accepted and tuple(candidate.bbox) in accepted_by_box:
            relabeled_candidates.append(
                LabelCandidate(
                    figure_id=candidate.figure_id,
                    label=accepted_by_box[tuple(candidate.bbox)],
                    bbox=candidate.bbox,
                    confidence=candidate.confidence,
                    filter_score=candidate.filter_score,
                    raw_ocr_confidence=candidate.raw_ocr_confidence,
                    accepted=True,
                    reason=candidate.reason,
                    rejection_reason=None,
                )
            )
        else:
            relabeled_candidates.append(candidate)
    return labels, relabeled_candidates


def _score_label_candidate(
    image,
    threshold,
    contour,
    bbox: list[int],
    raw_boxes: list[list[int]],
) -> dict:
    x, y, width, height = bbox
    area = cv2.contourArea(contour)
    fill_ratio = area / max(1, width * height)
    mean_bgr = _mean_component_color(image=image, contour=contour)
    max_channel = max(mean_bgr)
    color_spread = max(mean_bgr) - min(mean_bgr)
    left_density = _ink_density(threshold, x - 120, y - 45, x, y + height + 45)
    top_density = _ink_density(threshold, x - 20, y - 80, x + width + 120, y)
    right_density = _ink_density(threshold, x + width, y, x + width + 160, y + height + 80)
    below_density = _ink_density(threshold, x, y + height, x + width + 180, y + height + 100)
    local_count = _nearby_candidate_count(bbox=bbox, raw_boxes=raw_boxes)

    positive_reasons = []
    negative_reasons = []
    positive_score = 0.0
    penalty = 0.0

    positive_score += 1.0
    positive_reasons.append("single uppercase candidate")
    if height >= 55:
        positive_score += 1.2
        positive_reasons.append("large font")
    elif height >= 45:
        positive_score += 0.6
        positive_reasons.append("medium font")
    else:
        penalty += 2.0
        negative_reasons.append("very small font")

    if area >= 650:
        positive_score += 0.8
        positive_reasons.append("strong ink area")
    elif area < 500:
        penalty += 0.7
        negative_reasons.append("low ink area")

    if fill_ratio >= 0.22:
        positive_score += 0.7
        positive_reasons.append("letter-like contour")
    else:
        penalty += 1.0
        negative_reasons.append("not letter-like")

    if max_channel <= 145 and color_spread <= 75:
        positive_score += 0.8
        positive_reasons.append("dark glyph")
    else:
        penalty += 2.0
        negative_reasons.append("colored/internal annotation")

    if left_density <= 0.10 and top_density <= 0.10:
        positive_score += 1.0
        positive_reasons.append("isolated upper-left anchor")
    else:
        penalty += min(1.0, left_density + top_density)
        negative_reasons.append("too close to surrounding text")

    if right_density <= 0.14 or below_density <= 0.14:
        positive_score += 0.4
        positive_reasons.append("near whitespace/panel boundary")

    if local_count >= 8:
        penalty += 1.4
        negative_reasons.append("inside dense image texture")
    elif local_count >= 4:
        penalty += 0.7
        negative_reasons.append("near dense plot content")

    filter_score = round(positive_score - penalty, 3)
    threshold = 3.2
    borderline_threshold = 2.5
    accepted = filter_score >= threshold

    reasons = []
    if height < 45:
        reasons.append("too small")
    if area < 600:
        reasons.append("low ink area")
    if fill_ratio < 0.22:
        reasons.append("not letter-like")
    if max_channel > 135 or color_spread > 65:
        reasons.append("colored/internal annotation")
    if left_density > 0.18 or top_density > 0.18:
        reasons.append("too close to surrounding text")
    if not accepted and filter_score >= borderline_threshold:
        reasons.append("borderline score")

    reason_text = (
        "positive: "
        + ", ".join(positive_reasons)
        + "; penalties: "
        + (", ".join(negative_reasons) if negative_reasons else "none")
    )
    return {
        "bbox": bbox,
        "confidence": round(max(0.0, min(filter_score / 5.0, 0.99)), 3),
        "raw_ocr_confidence": None,
        "filter_score": filter_score,
        "accepted": accepted,
        "reason": reason_text,
        "rejection_reason": "; ".join(reasons) if reasons else None,
        "contour": contour,
        "debug_features": {
            "area": round(area, 1),
            "fill_ratio": round(fill_ratio, 3),
            "mean_bgr": [round(float(value), 1) for value in mean_bgr],
            "left_density": round(left_density, 3),
            "top_density": round(top_density, 3),
            "right_density": round(right_density, 3),
            "below_density": round(below_density, 3),
            "nearby_candidate_count": local_count,
        },
    }


def _group_candidate_dicts_into_rows(
    candidates: list[dict],
    row_tolerance: int = 150,
) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for candidate in sorted(candidates, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        x, y, width, height = candidate["bbox"]
        center_y = y + height / 2
        placed = False
        for row in rows:
            row_center = sum(item["bbox"][1] + item["bbox"][3] / 2 for item in row) / len(row)
            if abs(center_y - row_center) <= row_tolerance:
                row.append(candidate)
                placed = True
                break
        if not placed:
            rows.append([candidate])
    return rows


def _apply_spatial_label_cleanup(
    candidates: list[dict],
    image_width: int,
    image_height: int,
) -> list[dict]:
    accepted_candidates = [candidate for candidate in candidates if candidate["accepted"]]
    rows = _group_candidate_dicts_into_rows(accepted_candidates, row_tolerance=max(70, int(image_height * 0.025)))
    rows.sort(key=lambda row: min(candidate["bbox"][1] for candidate in row))

    internal_boxes = set()
    kept_rows: list[list[dict]] = []
    for row in rows:
        row_min_x = min(candidate["bbox"][0] for candidate in row)
        row_min_y = min(candidate["bbox"][1] for candidate in row)
        starts_far_inside = row_min_x > image_width * 0.12
        if starts_far_inside and len(row) >= 3:
            internal_boxes.update(tuple(candidate["bbox"]) for candidate in row)
            continue
        previous_row = kept_rows[-1] if kept_rows else None
        if previous_row is not None:
            previous_min_y = min(candidate["bbox"][1] for candidate in previous_row)
            previous_len = len(previous_row)
            vertical_gap = row_min_y - previous_min_y
            close_to_previous_panel_row = vertical_gap < image_height * 0.28
            row_is_short_internal = len(row) <= 3 and starts_far_inside
            if previous_len >= 3 and row_is_short_internal and close_to_previous_panel_row:
                internal_boxes.update(tuple(candidate["bbox"]) for candidate in row)
                continue
        kept_rows.append(row)

    updated = []
    for candidate in candidates:
        if tuple(candidate["bbox"]) in internal_boxes:
            reason = candidate["rejection_reason"]
            reason = "inside an existing panel region" if reason is None else f"{reason}; inside an existing panel region"
            updated.append({
                **candidate,
                "accepted": False,
                "rejection_reason": reason,
                "reason": candidate["reason"] + "; spatial cleanup: inside existing panel region",
            })
        else:
            updated.append(candidate)
    return updated


def _mean_component_color(image, contour) -> list[float]:
    mask = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask[:, :] = 0
    cv2.drawContours(mask, [contour], -1, 255, -1)
    pixels = image[mask.astype(bool)]
    if len(pixels) == 0:
        return [255.0, 255.0, 255.0]
    return [float(value) for value in pixels.mean(axis=0)]


def _ink_density(threshold, x1: int, y1: int, x2: int, y2: int) -> float:
    image_height, image_width = threshold.shape[:2]
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(image_width, x2)
    y2 = min(image_height, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    area = (x2 - x1) * (y2 - y1)
    return cv2.countNonZero(threshold[y1:y2, x1:x2]) / area


def _nearby_candidate_count(bbox: list[int], raw_boxes: list[list[int]]) -> int:
    if not raw_boxes:
        return 0
    x, y, width, height = bbox
    center_x = x + width / 2
    center_y = y + height / 2
    count = 0
    for other in raw_boxes:
        if other == bbox:
            continue
        other_x, other_y, other_width, other_height = other
        other_center_x = other_x + other_width / 2
        other_center_y = other_y + other_height / 2
        if abs(center_x - other_center_x) <= 520 and abs(center_y - other_center_y) <= 350:
            count += 1
    return count


def sort_label_candidate_dicts_spatially(candidates: list[dict]) -> list[dict]:
    sorted_boxes = sort_candidate_boxes_spatially([candidate["bbox"] for candidate in candidates])
    by_box = {tuple(candidate["bbox"]): candidate for candidate in candidates}
    return [by_box[tuple(box)] for box in sorted_boxes]


def _guess_candidate_label(index: int) -> str:
    if index < len(string.ascii_uppercase):
        return string.ascii_uppercase[index]
    return "unknown"


def _has_nearby_text(threshold, bbox: list[int]) -> bool:
    x, y, width, height = bbox
    image_height, image_width = threshold.shape[:2]
    pad_x = max(18, int(width * 0.65))
    pad_y = max(8, int(height * 0.2))
    left = max(0, x - pad_x)
    right = min(image_width, x + width + pad_x)
    top = max(0, y - pad_y)
    bottom = min(image_height, y + height + pad_y)

    contours, _ = cv2.findContours(
        threshold[top:bottom, left:right],
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    nearby_components = 0
    for contour in contours:
        cx, cy, cw, ch = cv2.boundingRect(contour)
        component_x = left + cx
        component_y = top + cy
        if abs(component_x - x) <= 2 and abs(component_y - y) <= 2:
            continue
        if cw >= 6 and ch >= 12:
            nearby_components += 1
    return nearby_components >= 2


def _panel_label_margin_score(x: int, y: int, image_width: int) -> float:
    band_width = max(240, int(image_width * 0.11))
    nearest_band_offset = min(x % band_width, band_width - (x % band_width))
    near_margin_bonus = 1.0 if nearest_band_offset < band_width * 0.22 else 0.0
    left_edge_bonus = 1.0 if x < image_width * 0.04 else 0.0
    top_position_bonus = 1.0 if y < 120 else 0.75
    score = 0.4 + 0.35 * near_margin_bonus + 0.15 * left_edge_bonus + 0.1 * top_position_bonus
    return min(score, 1.0)


def _dedupe_label_candidates(candidates: list[dict]) -> list[dict]:
    deduped = []
    for candidate in sorted(candidates, key=lambda item: item["confidence"], reverse=True):
        bbox = candidate["bbox"]
        if any(_intersection_area(bbox, existing["bbox"]) / (bbox[2] * bbox[3]) > 0.5 for existing in deduped):
            continue
        deduped.append(candidate)
    return deduped


def _create_panel_regions_from_labels(
    image,
    labels: list[PanelLabel],
    layout_override: list[list[str]] | None,
) -> tuple[list[list[int]], dict]:
    image_height, image_width = image.shape[:2]
    sorted_labels = sort_candidate_boxes_spatially([label.bbox for label in labels])
    labels_by_box = {tuple(label.bbox): label for label in labels}
    ordered_labels = [labels_by_box[tuple(bbox)] for bbox in sorted_labels]
    rows = _build_label_rows(
        labels=ordered_labels,
        image_height=image_height,
        layout_override=layout_override,
    )

    row_tops = []
    for row in rows:
        row_min_y = min(label.bbox[1] for label in row)
        row_tops.append(max(0, row_min_y - int(image_height * 0.01)))

    row_bottoms = []
    for index, row in enumerate(rows):
        if index + 1 < len(row_tops):
            row_bottoms.append(max(row_tops[index] + 1, row_tops[index + 1] - int(image_height * 0.006)))
        else:
            row_bottoms.append(image_height)

    panel_regions = []
    layout_rows = []
    for row_index, row in enumerate(rows):
        row = sorted(row, key=lambda label: label.bbox[0])
        row_top = row_tops[row_index]
        row_bottom = row_bottoms[row_index]
        row_labels = []
        for label_index, label in enumerate(row):
            if label_index == 0:
                left = 0
            else:
                left = max(0, label.bbox[0] - 18)

            if label_index + 1 < len(row):
                right = max(left + 1, row[label_index + 1].bbox[0] - 18)
            else:
                right = image_width

            panel_regions.append(
                [
                    int(left),
                    int(row_top),
                    int(right - left),
                    int(row_bottom - row_top),
                ]
            )
            row_labels.append(label.label)

        layout_rows.append(
            {
                "labels": row_labels,
                "top": int(row_top),
                "bottom": int(row_bottom),
            }
        )

    return panel_regions, {
        "rows": [row["labels"] for row in layout_rows],
        "row_boundaries": layout_rows,
        "source": "manual_override" if layout_override else "auto_cluster",
    }


def _build_label_rows(
    labels: list[PanelLabel],
    image_height: int,
    layout_override: list[list[str]] | None,
) -> list[list[PanelLabel]]:
    if layout_override:
        labels_by_name = {label.label: label for label in labels}
        rows = []
        for row in layout_override:
            label_row = [labels_by_name[label] for label in row if label in labels_by_name]
            if label_row:
                rows.append(sorted(label_row, key=lambda label: label.bbox[0]))
        used_labels = {label.label for row in rows for label in row}
        unused = [label for label in labels if label.label not in used_labels]
        if unused:
            rows.extend(_group_labels_into_rows(unused, image_height=image_height))
        return rows

    return _group_labels_into_rows(labels, image_height=image_height)


def _group_labels_into_rows(
    labels: list[PanelLabel],
    image_height: int,
) -> list[list[PanelLabel]]:
    if not labels:
        return []

    row_tolerance = max(int(image_height * 0.08), 80)
    rows: list[list[PanelLabel]] = []
    for label in sorted(labels, key=lambda item: (item.bbox[1], item.bbox[0])):
        label_center_y = label.bbox[1] + label.bbox[3] / 2
        placed = False
        for row in rows:
            row_center_y = sum(item.bbox[1] + item.bbox[3] / 2 for item in row) / len(row)
            if abs(label_center_y - row_center_y) <= row_tolerance:
                row.append(label)
                placed = True
                break
        if not placed:
            rows.append([label])

    rows.sort(key=lambda row: min(label.bbox[1] for label in row))
    return rows


def _detect_candidate_boxes(image) -> list[list[int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, threshold = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 17))
    closed = cv2.morphologyEx(threshold, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    image_height, image_width = image.shape[:2]
    image_area = image_width * image_height
    min_area = max(900, int(image_area * 0.004))
    max_area = int(image_area * 0.92)

    candidate_boxes = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = width * height
        if area < min_area or area > max_area:
            continue
        if width < 40 or height < 40:
            continue
        candidate_boxes.append([int(x), int(y), int(width), int(height)])

    candidate_boxes = _remove_nested_boxes(candidate_boxes)
    return sort_candidate_boxes_spatially(candidate_boxes)


def _remove_nested_boxes(boxes: list[list[int]]) -> list[list[int]]:
    filtered = []
    for box in boxes:
        box_area = box[2] * box[3]
        is_nested = False
        for other in boxes:
            if box == other:
                continue
            other_area = other[2] * other[3]
            if other_area <= box_area:
                continue
            overlap_area = _intersection_area(box, other)
            if overlap_area / box_area > 0.9:
                is_nested = True
                break
        if not is_nested:
            filtered.append(box)
    return filtered


def _intersection_area(first: list[int], second: list[int]) -> int:
    first_x, first_y, first_width, first_height = first
    second_x, second_y, second_width, second_height = second
    left = max(first_x, second_x)
    top = max(first_y, second_y)
    right = min(first_x + first_width, second_x + second_width)
    bottom = min(first_y + first_height, second_y + second_height)
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def _crop_panel(image, bbox: list[int], output_file: Path) -> None:
    x, y, width, height = bbox
    crop = image[y : y + height, x : x + width]
    cv2.imwrite(str(output_file), crop)


def _write_debug_image(image, boxes: list[list[int]], output_file: Path) -> None:
    debug_image = image.copy()
    for index, (x, y, width, height) in enumerate(boxes):
        panel_label = string.ascii_uppercase[index]
        cv2.rectangle(debug_image, (x, y), (x + width, y + height), (0, 0, 255), 4)
        cv2.putText(
            debug_image,
            panel_label,
            (x + 10, y + 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (0, 0, 255),
            3,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(output_file), debug_image)


def _write_label_debug_image(image, labels: list[PanelLabel], output_file: Path) -> None:
    debug_image = image.copy()
    for label in labels:
        x, y, width, height = label.bbox
        cv2.rectangle(debug_image, (x, y), (x + width, y + height), (255, 0, 0), 4)
        cv2.putText(
            debug_image,
            label.label,
            (x, max(35, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (255, 0, 0),
            3,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(output_file), debug_image)


def _write_candidate_debug_image(
    image,
    candidates: list[LabelCandidate],
    output_file: Path,
) -> None:
    debug_image = image.copy()
    for candidate in candidates:
        x, y, width, height = candidate.bbox
        if candidate.accepted:
            color = (0, 160, 0)
        elif candidate.filter_score >= 1.75:
            color = (0, 220, 220)
        else:
            color = (0, 0, 255)
        cv2.rectangle(debug_image, (x, y), (x + width, y + height), color, 4)
        label_text = f"{candidate.label} {candidate.filter_score:.1f}"
        if not candidate.accepted and candidate.rejection_reason:
            label_text = f"{candidate.label} {candidate.filter_score:.1f}: {candidate.rejection_reason[:24]}"
        cv2.putText(
            debug_image,
            label_text,
            (x, max(35, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(output_file), debug_image)


def _write_region_debug_image(image, panels: list[list[int]], output_file: Path) -> None:
    debug_image = image.copy()
    for index, (x, y, width, height) in enumerate(panels):
        label = string.ascii_uppercase[index]
        cv2.rectangle(debug_image, (x, y), (x + width, y + height), (0, 0, 255), 5)
        cv2.putText(
            debug_image,
            label,
            (x + 20, y + 55),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.5,
            (0, 0, 255),
            4,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(output_file), debug_image)


def _write_row_cluster_debug_image(
    image,
    labels: list[PanelLabel],
    panels: list[list[int]],
    layout_metadata: dict | None,
    output_file: Path,
) -> None:
    debug_image = image.copy()
    colors = [
        (0, 0, 255),
        (0, 160, 0),
        (255, 0, 0),
        (0, 180, 180),
        (180, 0, 180),
        (180, 120, 0),
    ]

    label_to_row = {}
    if layout_metadata:
        for row_index, row in enumerate(layout_metadata["rows"]):
            for label in row:
                label_to_row[label] = row_index
        for row_index, row_boundary in enumerate(layout_metadata["row_boundaries"]):
            color = colors[row_index % len(colors)]
            top = row_boundary["top"]
            bottom = row_boundary["bottom"]
            cv2.line(debug_image, (0, top), (image.shape[1], top), color, 5)
            cv2.line(debug_image, (0, bottom), (image.shape[1], bottom), color, 5)
            cv2.putText(
                debug_image,
                f"row {row_index + 1}: {','.join(row_boundary['labels'])}",
                (20, max(45, top + 45)),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                color,
                3,
                cv2.LINE_AA,
            )

    for index, (x, y, width, height) in enumerate(panels):
        color = colors[index % len(colors)]
        cv2.rectangle(debug_image, (x, y), (x + width, y + height), color, 4)

    for label in labels:
        x, y, width, height = label.bbox
        row_index = label_to_row.get(label.label, 0)
        color = colors[row_index % len(colors)]
        cv2.rectangle(debug_image, (x, y), (x + width, y + height), color, 5)
        cv2.putText(
            debug_image,
            f"{label.label}/R{row_index + 1}",
            (x, max(35, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            color,
            2,
            cv2.LINE_AA,
        )

    cv2.imwrite(str(output_file), debug_image)


def _figure_result(
    figure_id: str,
    source_image: Path,
    expected_panel_count: int | None,
    detected_panel_count: int,
    warnings: list[str],
    panels: list[dict],
    debug_image: Path | None,
    detection_method: str,
    confidence: str,
    detected_labels: list[PanelLabel],
    rejected_candidates: list[LabelCandidate] | None = None,
) -> dict:
    matched = (
        detected_panel_count == expected_panel_count
        if expected_panel_count is not None
        else None
    )
    result_warnings = list(warnings)

    if expected_panel_count is None:
        result_warnings.append(
            "No expected panel count was provided in input/expected_panels.json. "
            "Count matching was not checked."
        )
    elif not matched:
        result_warnings.append(
            "Detected panel count does not match expected count. Marked for manual review."
        )

    needs_manual_review = confidence == "low" or matched is False
    if needs_manual_review:
        status = "needs_manual_review"
    elif matched is None:
        status = "ok_unverified"
    else:
        status = "ok"

    return {
        "figure_id": figure_id,
        "source_image": str(source_image),
        "expected_panel_count": expected_panel_count,
        "detected_label_count": len(detected_labels),
        "detected_panel_count": detected_panel_count,
        "final_panel_count": detected_panel_count,
        "matched_expected_count": matched,
        "detection_method": detection_method,
        "confidence": confidence,
        "needs_manual_review": needs_manual_review,
        "status": status,
        "warnings": result_warnings,
        "detected_label_names": [label.label for label in detected_labels],
        "missing_expected_labels": _missing_expected_labels(
            expected_panel_count=expected_panel_count,
            detected_labels=detected_labels,
        ),
        "rejected_label_candidates": [
            _candidate_to_dict(candidate)
            for candidate in (rejected_candidates or [])
        ],
        "detected_labels": [
            {
                "label": label.label,
                "bbox": label.bbox,
                "confidence": label.confidence,
            }
            for label in detected_labels
        ],
        "panels": panels,
        "debug_image": str(debug_image) if debug_image else None,
    }


def _missing_expected_labels(
    expected_panel_count: int | None,
    detected_labels: list[PanelLabel],
) -> list[str]:
    if expected_panel_count is None:
        return []
    expected_labels = list(string.ascii_uppercase[:expected_panel_count])
    detected_label_names = {label.label for label in detected_labels}
    return [label for label in expected_labels if label not in detected_label_names]


def _candidate_to_dict(candidate: LabelCandidate) -> dict:
    return {
        "figure_id": candidate.figure_id,
        "label": candidate.label,
        "bbox": candidate.bbox,
        "confidence": candidate.confidence,
        "raw_ocr_confidence": candidate.raw_ocr_confidence,
        "filter_score": candidate.filter_score,
        "accepted": candidate.accepted,
        "reason": candidate.reason,
        "rejection_reason": candidate.rejection_reason,
    }


def _write_detection_report(results: list[dict], output_path: Path) -> None:
    lines = [
        "# Panel Detection Report",
        "",
        "This report summarizes Phase 3 label-anchored panel detection.",
        "",
    ]

    if not results:
        lines.extend(
            [
                "No manually loaded figures were available for panel detection.",
                "",
                "Create `input/figs/` and save complete figure images named `fig1.png`, "
                "`fig2.png`, etc., then run the pipeline again.",
                "",
            ]
        )

    if results:
        lines.extend(
            [
                "## Summary",
                "",
                "| Figure | Expected | Detected Labels | Final Panels | Method | Needs Review |",
                "|--------|----------|-----------------|--------------|--------|--------------|",
            ]
        )
        for result in results:
            expected = (
                "null"
                if result["expected_panel_count"] is None
                else str(result["expected_panel_count"])
            )
            lines.append(
                f"| {result['figure_id']} | {expected} | {result['detected_label_count']} | "
                f"{result['final_panel_count']} | {result['detection_method']} | "
                f"{str(result['needs_manual_review']).lower()} |"
            )
        lines.append("")

    for result in results:
        source_figure_name = Path(result["source_image"]).name
        expected = (
            "null"
            if result["expected_panel_count"] is None
            else str(result["expected_panel_count"])
        )
        matched = (
            "unknown"
            if result["matched_expected_count"] is None
            else "yes"
            if result["matched_expected_count"]
            else "no"
        )
        lines.extend(
            [
                f"## {result['figure_id']}",
                "",
                f"- Source figure name: `{source_figure_name}`",
                f"- Source image path: `{result['source_image']}`",
                f"- Expected panel count: {expected}",
                f"- Detected label count: {result['detected_label_count']}",
                f"- Detected labels: {', '.join(result['detected_label_names']) or 'none'}",
                f"- Missing expected labels: {', '.join(result['missing_expected_labels']) or 'none'}",
                f"- Final panel count: {result['final_panel_count']}",
                f"- Count matched: {matched}",
                f"- Detection method: `{result['detection_method']}`",
                f"- Confidence: `{result['confidence']}`",
                f"- Needs manual review: {'yes' if result['needs_manual_review'] else 'no'}",
                f"- Status: `{result['status']}`",
                "- Warnings:",
            ]
        )
        if result["warnings"]:
            lines.extend(f"  - {warning}" for warning in result["warnings"])
        else:
            lines.append("  - None")

        lines.append("- Rejected label candidates:")
        rejected = result["rejected_label_candidates"]
        if rejected:
            for candidate in rejected[:20]:
                lines.append(
                    f"  - `{candidate['label']}` bbox={candidate['bbox']} "
                    f"reason={candidate['rejection_reason']}"
                )
            if len(rejected) > 20:
                lines.append(f"  - ... {len(rejected) - 20} more")
        else:
            lines.append("  - None")

        lines.append("- Cropped output file names:")
        if result["panels"]:
            lines.extend(f"  - `{panel['output_file']}`" for panel in result["panels"])
        else:
            lines.append("  - None")
        lines.append("")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _normalize_figure_id(figure_id: str) -> str:
    figure_id = figure_id.strip()
    if not figure_id:
        raise ValueError("Figure ID cannot be blank. Example: Fig1")
    match = FIGURE_ID_PATTERN.match(figure_id)
    if match is not None:
        return f"Fig{int(match.group(1))}"
    return figure_id
