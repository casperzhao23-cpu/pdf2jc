# Architecture

PDF2JC uses inspectable intermediate artifacts instead of treating slide generation as a single opaque operation.

1. **PDF extraction** writes page text and page images.
2. **Manual figure loading** standardizes complete figure images supplied by the researcher.
3. **Panel detection** creates candidate panel crops, bounding boxes, label candidates, layout metadata, and debug overlays.
4. **Citation mapping** links citation sentences to available panel images.
5. **Narrative and evidence building** retains Results-section and paragraph context while producing reviewable slide objects.
6. **Presentation building** renders editable text and individual panel-image objects into PowerPoint.

The local `output/` directory contains these artifacts and is intentionally excluded from Git because it may contain source-paper content and generated presentation files.
