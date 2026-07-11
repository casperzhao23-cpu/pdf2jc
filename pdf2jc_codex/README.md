# PDF2JC

PDF2JC turns a biomedical research article into an editable journal club PowerPoint draft. Rather than jumping straight from a paper to slides, it reconstructs the paper's experimental narrative first: panels are connected to citation sentences, citation sentences are organized into narrative and evidence units, and evidence units become slide objects.

The result is a reviewable intermediate record as well as an editable `.pptx` deck. Text, titles, captions, labels, and individual panel images remain separate PowerPoint objects for manual refinement before presentation.

## Key features

- **Manual figure input and panel detection**: load complete figures that you save from a paper, then create panel-level crops with diagnostic images.
- **Citation mapping**: normalize in-text citations such as `Fig. 2A-C` and connect them to the corresponding panel images.
- **Narrative unit reconstruction**: retain Results-section and paragraph context around each piece of evidence.
- **Evidence unit generation**: group panels only when their citation sentence and local experimental story support that grouping.
- **Slide object architecture**: generate inspectable JSON, HTML, and CSV slide proposals before rendering a deck.
- **Editable journal club PowerPoint generation**: render each panel as a separate image and all text as editable PowerPoint text boxes.

## Pipeline architecture

```text
Paper PDF
  |
  v
Panel Detection
  |
  v
Citation Mapping
  |
  v
Narrative Units
  |
  v
Evidence Units
  |
  v
Slide Objects
  |
  v
Presentation Builder
  |
  v
Journal Club PowerPoint
```

## Installation

PDF2JC requires Python 3.10 or newer. The optional presentation renderer also expects a current Node.js installation with the presentation artifact runtime available in the environment.

```bash
git clone <your-repository-url>
cd pdf2jc
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Alternatively, install the runtime dependencies directly:

```bash
python -m pip install -r requirements.txt
```

## Quick start

1. Create a local input folder. It is deliberately ignored by Git so research papers and locally extracted figures stay private.

```bash
mkdir -p input/figs
```

2. Put your article at `input/paper.pdf`. Manually save each complete figure as `input/figs/fig1.png`, `input/figs/fig2.png`, and so on. Supported image formats are PNG, JPG/JPEG, TIF, and TIFF.

3. Optionally add expected panel counts at `input/expected_panels.json`:

```json
{
  "Fig1": 8,
  "Fig2": 6
}
```

4. Run the pipeline stages:

```bash
# Extract article text, standardize manual figures, detect panels, and create the baseline draft.
pdf2jc run --pdf input/paper.pdf --figures-dir input/figs --output-dir output

# Map citation sentences to detected panels and export citation QC files.
pdf2jc diagnose-citations --output-dir output

# Build narrative units, evidence units, and reviewable slide objects.
pdf2jc diagnose-evidence-units --output-dir output

# Render editable PowerPoint slides from the slide objects.
pdf2jc build-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
```

When running from a source checkout without installing the package, prefix commands with `PYTHONPATH=src python -m`, for example:

```bash
PYTHONPATH=src python -m pdf2jc diagnose-panels --pdf input/paper.pdf --figures-dir input/figs --output-dir output
```

## Example workflow

The `examples/sample_input/figs/` folder contains figure-image examples only. Copy them into a local `input/figs/` folder to explore panel diagnosis. PDFs are not distributed with the repository; use a paper that you are permitted to process and share.

Inspect these quality-control outputs after a run:

- `output/debug/` for label and panel-region overlays
- `output/panel_detection_report.md` for panel detection status
- `output/citation_qc_table.html` for sentence-to-panel mapping review
- `output/slide_review.html` for narrative, evidence, and slide-object review

## CLI commands

```bash
# Detect panel crops for all manual figures and print a status table.
pdf2jc diagnose-panels --pdf input/paper.pdf --figures-dir input/figs --output-dir output

# Build and inspect citation-to-panel mappings.
pdf2jc diagnose-citations --output-dir output
pdf2jc export-citation-qc --output-dir output

# Build and inspect narrative, evidence, and slide objects.
pdf2jc diagnose-slides --output-dir output
pdf2jc diagnose-evidence-units --output-dir output

# Render and inspect the editable presentation.
pdf2jc build-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
pdf2jc diagnose-presentation --output-dir output --grouping-mode sentence_grouped --theme theme.yaml
```

## Repository structure

```text
pdf2jc/
├── README.md
├── LICENSE
├── pyproject.toml
├── requirements.txt
├── theme.yaml
├── src/
│   ├── load_manual_figures.py
│   └── pdf2jc/
├── tests/
├── examples/
│   ├── sample_input/
│   └── sample_output/
├── docs/
└── assets/
```

`input/` and `output/` are created locally at runtime and excluded from version control.

## Roadmap

- Improve figure-panel boundary inference and add user-friendly panel-layout overrides.
- Strengthen Results-section parsing and citation coverage for whole-figure references.
- Add richer biological claim extraction and slide-layout alternatives.
- Package a fully self-contained presentation renderer for standard Python environments.

## Citation

If you use PDF2JC in research, please cite the repository release:

```text
PDF2JC contributors. PDF2JC: reconstructing biomedical experimental narratives for journal club presentations. Version 0.1.0. 2026.
```

## Acknowledgements

PDF2JC is built with PyMuPDF, OpenCV, Pydantic, and the presentation artifact runtime. We thank the researchers and journal-club communities whose feedback shapes the project.

## License

PDF2JC is released under the [MIT License](LICENSE).
