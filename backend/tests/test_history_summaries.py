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
