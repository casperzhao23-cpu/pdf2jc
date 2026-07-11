"""Load manually saved complete figure images into standardized output files."""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import cv2
except ModuleNotFoundError:  # pragma: no cover - exercised only before install
    cv2 = None


ACCEPTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
FIGURE_ID_PATTERN = re.compile(r"^(?:fig|figure)[_\-\s]*(\d+)$", re.IGNORECASE)


def load_manual_figures(input_figures_dir: Path, output_dir: Path) -> Path:
    if cv2 is None:
        raise RuntimeError(
            "OpenCV is not installed yet. Run: python -m pip install -r requirements.txt"
        )

    if not input_figures_dir.exists():
        raise FileNotFoundError(
            f"Manual figure folder not found: {input_figures_dir}. "
            "Please create input/figs/ and save complete figure images there, "
            "for example input/figs/fig1.png and input/figs/fig2.png."
        )

    image_paths = sorted(
        path
        for path in input_figures_dir.iterdir()
        if path.is_file() and path.suffix.lower() in ACCEPTED_EXTENSIONS
    )
    if not image_paths:
        raise FileNotFoundError(
            f"No figure images were found in {input_figures_dir}. "
            "Please save complete figure images named fig1.png, fig2.png, etc. "
            "into input/figs/ before running the pipeline."
        )

    manual_figures_dir = output_dir / "manual_figures"
    manual_figures_dir.mkdir(parents=True, exist_ok=True)
    for old_figure in manual_figures_dir.glob("Fig*.png"):
        old_figure.unlink()

    loaded_figures = []
    for image_path in image_paths:
        figure_id = detect_figure_id(image_path)
        if figure_id is None:
            loaded_figures.append(
                {
                    "figure_id": None,
                    "source_path": str(image_path),
                    "output_path": None,
                    "status": "skipped",
                    "warning": (
                        "Filename does not look like fig1.png, fig2.jpg, "
                        "figure3.png, or Figure_4.tif."
                    ),
                }
            )
            continue

        output_path = manual_figures_dir / f"{figure_id}.png"
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            loaded_figures.append(
                {
                    "figure_id": figure_id,
                    "source_path": str(image_path),
                    "output_path": str(output_path),
                    "status": "skipped",
                    "warning": "Could not open this image. Try saving it as PNG or JPG.",
                }
            )
            continue

        cv2.imwrite(str(output_path), image)
        loaded_figures.append(
            {
                "figure_id": figure_id,
                "source_path": str(image_path),
                "output_path": str(output_path),
                "status": "loaded",
            }
        )

    loaded_count = sum(1 for figure in loaded_figures if figure["status"] == "loaded")
    if loaded_count == 0:
        raise FileNotFoundError(
            "No usable figure images were loaded. Please name files like fig1.png, "
            "fig2.png, figure3.jpg, or Figure_4.tif inside input/figs/."
        )

    metadata_path = output_dir / "manual_figures.json"
    metadata_path.write_text(
        json.dumps(loaded_figures, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata_path


def detect_figure_id(path: Path) -> str | None:
    match = FIGURE_ID_PATTERN.match(path.stem)
    if match is None:
        return None
    return f"Fig{int(match.group(1))}"
