# PDF2JC Web Architecture

The website is a thin graphical entry point for the existing PDF2JC Python
pipeline. It must not replace or simplify the pipeline stages in `src/pdf2jc/`.

## Processing order

The web run follows the project pipeline in this order:

```text
Paper PDF
  -> PDF text extraction
  -> manual figure upload
  -> panel detection
  -> citation mapping
  -> narrative units
  -> evidence units
  -> slide objects
  -> presentation builder
  -> editable journal club PowerPoint
```

## Responsibilities

### Existing Python pipeline

The existing modules remain responsible for scientific and presentation logic:

- `pdf_extractor.py`: extracts text from the paper PDF.
- `load_manual_figures.py`: standardizes complete figure images that users
  manually save and upload.
- `panel_detector.py`: detects panel labels, crops panels, and writes debug
  overlays.
- `citation_mapper.py`: maps paper citation sentences to detected panel images
  and writes citation QC files.
- `narrative_builder.py`: builds narrative units, evidence units, slide objects,
  and slide review files.
- `presentation_builder.py`: renders slide objects into an editable PowerPoint
  where text is editable and each panel is inserted as a separate image object.

### Web layer

The web layer in `src/pdf2jc/web.py` is deliberately small. It:

1. accepts an uploaded paper PDF;
2. accepts manually uploaded complete figure images;
3. optionally accepts expected panel counts JSON;
4. creates an isolated local job folder under `output/web_jobs/`;
5. calls the existing pipeline stage functions in order;
6. tracks per-stage status in `status.json`;
7. serves existing review artifacts:
   - panel debug images from `output/debug/`;
   - citation QC from `output/citation_qc_table.html`;
   - slide review from `output/slide_review.html`;
   - final PowerPoint from `output/jc_draft.pptx`.

It does not perform automatic figure extraction from the PDF and does not
duplicate panel detection, citation mapping, narrative grouping, evidence-unit
grouping, slide-object construction, or PowerPoint rendering logic.

## Local command

```bash
pdf2jc web --host 127.0.0.1 --port 8765
```

When running from a source checkout without installation:

```bash
PYTHONPATH=src python -m pdf2jc web --host 127.0.0.1 --port 8765
```

Open the printed local URL, upload the paper and complete figure images, then
review the generated artifacts in the browser.
