import gzip
import io
import json
from pathlib import Path

import pytest
from PIL import Image, features

from app.history_content import (
    HISTORY_CONTENT_HASH_PREFIX,
    HistoryContentError,
    canonicalize_history_document,
    canonicalize_history_document_value,
    decompress_history_document,
    validate_history_image,
)

SHARED = Path(__file__).resolve().parents[2] / "shared"


def test_shared_canonicalization_fixtures_match_python_contract():
    fixtures = json.loads((SHARED / "history-canonicalization-v1.json").read_text())
    assert fixtures["hash_prefix"].encode() == HISTORY_CONTENT_HASH_PREFIX

    for fixture in fixtures["cases"]:
        canonical = canonicalize_history_document(fixture["canonical_json"].encode())
        assert canonical.canonical_bytes.decode() == fixture["canonical_json"], fixture["name"]
        assert canonical.content_hash == fixture["content_hash"], fixture["name"]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":1,"schema_version":1}',
        b"\xff",
        b"[]",
    ],
)
def test_canonicalization_rejects_ambiguous_or_invalid_json(payload):
    with pytest.raises(HistoryContentError):
        canonicalize_history_document(payload)


def test_canonicalization_rejects_unknown_fields_and_limits():
    valid = {
        "schema_version": 1,
        "extraction_version": "history-dom-v2",
        "content_type": "article",
        "language": "en",
        "blocks": [{"id": "b0001", "kind": "paragraph", "text": "useful text"}],
    }
    with pytest.raises(HistoryContentError, match="unknown or missing"):
        canonicalize_history_document_value({**valid, "html": "<script />"})
    with pytest.raises(HistoryContentError, match="too long"):
        canonicalize_history_document_value(
            {
                **valid,
                "blocks": [
                    {
                        "id": "b0001",
                        "kind": "paragraph",
                        "text": "x" * 8_001,
                    }
                ],
            }
        )


def test_gzip_decompression_is_bounded():
    compressed = gzip.compress(b"x" * 10_000)

    with pytest.raises(HistoryContentError, match="size limit"):
        decompress_history_document(compressed, max_bytes=100)
    assert decompress_history_document(gzip.compress(b"valid"), max_bytes=100) == b"valid"


@pytest.mark.parametrize(
    ("image_format", "expected"),
    [("PNG", "png"), ("JPEG", "jpeg"), ("WEBP", "webp")],
)
def test_image_validation_accepts_bounded_rasters(image_format, expected):
    if image_format == "WEBP" and not features.check("webp"):
        pytest.skip("Pillow was built without WebP support")
    output = io.BytesIO()
    Image.new("RGB", (32, 20), "navy").save(output, format=image_format)

    validated = validate_history_image(output.getvalue(), max_bytes=200 * 1024)

    assert validated.format == expected
    assert (validated.width, validated.height) == (32, 20)


def test_image_validation_rejects_svg_and_dimension_or_byte_overflow():
    with pytest.raises(HistoryContentError, match="valid raster"):
        validate_history_image(b"<svg><script /></svg>", max_bytes=200 * 1024)

    output = io.BytesIO()
    Image.new("RGB", (641, 1), "red").save(output, format="PNG")
    with pytest.raises(HistoryContentError, match="dimensions"):
        validate_history_image(output.getvalue(), max_bytes=200 * 1024)

    with pytest.raises(HistoryContentError, match="byte limit"):
        validate_history_image(b"x" * 11, max_bytes=10)
