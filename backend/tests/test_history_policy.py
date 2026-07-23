import pytest

from app.history_policy import (
    HistoryCaptureError,
    sanitize_capture_text,
    validate_normalized_history_url,
)


def test_normalized_url_accepts_safe_public_http_url():
    normalized = validate_normalized_history_url(
        "https://example.com/article?id=42&topic=local%20models"
    )
    assert normalized.url == "https://example.com/article?id=42&topic=local%20models"
    assert normalized.hostname == "example.com"
    assert len(normalized.url_hash) == 64


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("javascript:alert(1)", "only http and https"),
        ("data:text/plain,hello", "only http and https"),
        ("http://127.0.0.1/", "private-network"),
        ("https://localhost/", "single-label"),
        ("http://intranet/", "single-label"),
        ("https://service.internal/", "reserved"),
        ("https://router.lan/", "reserved"),
        ("https://site.test/", "reserved"),
        ("https://example.com/article#private", "fragments"),
        ("https://example.com/?utm_source=newsletter", "sensitive query"),
        ("https://example.com/?access_token=secret", "sensitive query"),
        ("https://EXAMPLE.com/", "lowercase"),
        ("https://example.com:443/", "default ports"),
        ("https://bücher.example.com/", "punycode"),
        ("https://example.com", "normalized path"),
    ],
)
def test_normalized_url_rejects_unsafe_or_extension_noncompliant_urls(url, message):
    with pytest.raises(HistoryCaptureError, match=message):
        validate_normalized_history_url(url)


def test_capture_text_strips_control_bidi_and_zero_width_characters():
    value = "hello\n<script>\u202eabc\u200b</script>\tworld"
    assert sanitize_capture_text(value) == "hello <script>abc</script> world"
