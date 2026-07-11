"""Mock article data used until real PDF parsing is implemented."""

from __future__ import annotations


def load_mock_article() -> dict:
    return {
        "title": "Mock Trial of Cardiometabolic Biomarker-Guided Therapy",
        "journal": "Example Journal of Translational Medicine",
        "year": 2026,
        "authors": [
            "A. Rivera",
            "M. Chen",
            "S. Patel",
            "L. Gomez",
        ],
        "clinical_question": (
            "In adults with elevated cardiometabolic risk, does biomarker-guided "
            "therapy improve 12-month risk factor control compared with usual care?"
        ),
        "design": "Multicenter, randomized, open-label, controlled trial",
        "population": "620 adults aged 40-75 with elevated cardiovascular risk",
        "intervention": "Biomarker-guided medication adjustment every 8 weeks",
        "comparator": "Usual primary care follow-up",
        "primary_outcome": "Composite improvement in blood pressure, LDL-C, and HbA1c at 12 months",
        "key_results": [
            "Primary outcome improved in 48% of intervention patients vs 34% of usual care patients.",
            "Absolute difference: 14 percentage points.",
            "No meaningful difference in serious adverse events was observed.",
        ],
        "limitations": [
            "Open-label design may introduce performance bias.",
            "Follow-up lasted only 12 months.",
            "The mock dataset does not include subgroup-level details.",
        ],
        "discussion_points": [
            "Would this workflow be practical in a busy clinic?",
            "Which patients would benefit most from biomarker-guided adjustment?",
            "What implementation barriers would matter locally?",
        ],
    }

