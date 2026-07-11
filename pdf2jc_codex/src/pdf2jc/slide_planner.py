"""Build a slide plan from article data."""

from __future__ import annotations


def build_slide_plan(article: dict) -> list[dict]:
    authors = ", ".join(article["authors"])
    return [
        {
            "title": "Journal Club Overview",
            "bullets": [
                article["title"],
                f"{article['journal']} ({article['year']})",
                f"Authors: {authors}",
            ],
        },
        {
            "title": "Clinical Question",
            "bullets": [
                article["clinical_question"],
                f"Population: {article['population']}",
            ],
        },
        {
            "title": "Study Design",
            "bullets": [
                article["design"],
                f"Intervention: {article['intervention']}",
                f"Comparator: {article['comparator']}",
            ],
        },
        {
            "title": "Key Results",
            "bullets": [
                article["primary_outcome"],
                *article["key_results"],
            ],
        },
        {
            "title": "Limitations",
            "bullets": article["limitations"],
        },
        {
            "title": "Discussion",
            "bullets": article["discussion_points"],
        },
    ]

