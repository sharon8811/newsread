import json

import pytest

from app.history_summaries import HistorySummaryOutputError, parse_history_summary

BLOCKS = [
    {"id": "b0001", "kind": "paragraph", "text": "First exact source sentence."},
    {"id": "b0002", "kind": "paragraph", "text": "Second exact source sentence."},
]


def test_summary_citations_are_derived_from_stored_blocks():
    markdown, citations = parse_history_summary(
        json.dumps(
            {
                "markdown": "The first claim is supported [1], as is the second [2].",
                "block_ids": ["b0001", "b0002"],
            }
        ),
        BLOCKS,
    )

    assert markdown.endswith("[2].")
    assert citations == [
        {
            "block_id": "b0001",
            "quote": "First exact source sentence.",
            "prefix": None,
            "suffix": None,
        },
        {
            "block_id": "b0002",
            "quote": "Second exact source sentence.",
            "prefix": None,
            "suffix": None,
        },
    ]


def test_summary_survives_prose_and_fences_around_the_json():
    markdown, citations = parse_history_summary(
        'Sure! Here you go:\n```json\n{"markdown": "A claim [1].", '
        '"block_ids": ["b0001"]}\n```\nHope that helps.',
        BLOCKS,
    )

    assert markdown == "A claim [1]."
    assert [citation["block_id"] for citation in citations] == ["b0001"]


def test_summary_keeps_only_the_sources_the_model_cited():
    markdown, citations = parse_history_summary(
        json.dumps(
            {
                "markdown": "Only the second source is used [2].",
                "block_ids": ["b0001", "b0002"],
                "notes": "an unexpected extra key",
            }
        ),
        BLOCKS,
    )

    assert markdown == "Only the second source is used [1]."
    assert [citation["block_id"] for citation in citations] == ["b0002"]


def test_summary_leaves_bracketed_numbers_that_are_not_citations_as_prose():
    markdown, citations = parse_history_summary(
        json.dumps(
            {
                "markdown": "Published in [2026], per the source [1].",
                "block_ids": ["b0001"],
            }
        ),
        BLOCKS,
    )

    assert markdown == "Published in [2026], per the source [1]."
    assert len(citations) == 1


def test_summary_drops_markers_pointing_at_unlisted_sources():
    markdown, citations = parse_history_summary(
        json.dumps(
            {
                "markdown": "A supported claim [1] and a dangling one [7].",
                "block_ids": ["b0001"],
            }
        ),
        BLOCKS,
    )

    assert markdown == "A supported claim [1] and a dangling one."
    assert len(citations) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"markdown": "Unknown [1].", "block_ids": ["b9999"]},
        {"markdown": "Missing marker.", "block_ids": ["b0001"]},
        {
            "markdown": "[Open](https://attacker.example) [1]",
            "block_ids": ["b0001"],
        },
        {"markdown": "<script>bad()</script> [1]", "block_ids": ["b0001"]},
        {"markdown": "```text\nbad [1]\n```", "block_ids": ["b0001"]},
        {"markdown": "Duplicate [1].", "block_ids": ["b0001", "b0001"]},
    ],
)
def test_summary_rejects_untrusted_or_inconsistent_citations(payload):
    with pytest.raises(HistorySummaryOutputError):
        parse_history_summary(json.dumps(payload), BLOCKS)
