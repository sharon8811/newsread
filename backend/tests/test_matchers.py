"""Offline matcher checks — plain asserts, no pytest dependency.

Run inside the backend container / venv:  python3 -m tests.test_matchers
"""

from app.enrichers import extract_links, match_url

MATCHES = [
    # github: bare, subpaths, .git, tracking params, case folding
    ("https://github.com/pytorch/pytorch", "github", "pytorch/pytorch"),
    ("https://www.github.com/PyTorch/PyTorch.git", "github", "pytorch/pytorch"),
    ("https://github.com/anthropics/claude-code/issues/74066", "github", "anthropics/claude-code"),
    ("https://github.com/foo/bar/blob/main/src/x.py?utm_source=hn#L10", "github", "foo/bar"),
    ("https://github.com/FossPrime/Steam-Controller-Auto-Charge", "github", "fossprime/steam-controller-auto-charge"),
    # hugging face models and datasets
    ("https://huggingface.co/Qwen/Qwen2.5-7B", "hf_model", "Qwen/Qwen2.5-7B"),
    ("https://hf.co/meta-llama/Llama-3.1-8B/tree/main", "hf_model", "meta-llama/Llama-3.1-8B"),
    ("https://huggingface.co/datasets/allenai/c4", "hf_dataset", "allenai/c4"),
    ("https://huggingface.co/datasets/HuggingFaceFW/fineweb/viewer", "hf_dataset", "HuggingFaceFW/fineweb"),
    # arxiv: abs/pdf/html, versions stripped, old-style ids
    ("https://arxiv.org/abs/1706.03762", "arxiv", "1706.03762"),
    ("https://arxiv.org/abs/1706.03762v7", "arxiv", "1706.03762"),
    ("https://www.arxiv.org/pdf/2301.12345.pdf", "arxiv", "2301.12345"),
    ("https://arxiv.org/html/2404.00001", "arxiv", "2404.00001"),
    ("https://arxiv.org/abs/cs/9901002", "arxiv", "cs/9901002"),
    # pypi: PEP 503 normalization
    ("https://pypi.org/project/requests/", "pypi", "requests"),
    ("https://pypi.org/project/Scrapling/0.4/", "pypi", "scrapling"),
    ("https://pypi.org/project/typing_extensions", "pypi", "typing-extensions"),
    # npm: plain, scoped, version suffix
    ("https://www.npmjs.com/package/react", "npm", "react"),
    ("https://npmjs.com/package/@babel/core", "npm", "@babel/core"),
    ("https://www.npmjs.com/package/next/v/16.2.10", "npm", "next"),
    # youtube: watch, short link, shorts, embed
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?feature=share", "youtube", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/abcdefghijk", "youtube", "abcdefghijk"),
    ("https://www.youtube.com/embed/abcdefghijk", "youtube", "abcdefghijk"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
]

NON_MATCHES = [
    "https://github.com/features",             # reserved section
    "https://github.com/torvalds",             # user page, 1 segment
    "https://gist.github.com/user/abc123",     # gists excluded
    "https://raw.githubusercontent.com/a/b/main/x.py",
    "https://huggingface.co/docs/transformers/index",  # reserved
    "https://huggingface.co/Qwen",             # org page
    "https://huggingface.co/datasets",         # section root
    "https://arxiv.org/list/cs.CL/recent",
    "https://pypi.org/user/someone/",
    "https://www.npmjs.com/~sindresorhus",
    "https://www.npmjs.com/search?q=react",
    "https://www.youtube.com/@veritasium",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/watch?v=too-short",
    "mailto:someone@example.com",
    "https://example.com/github.com/foo/bar",  # host must match, not path
    "",
]


def test_matchers() -> None:
    main()


def main() -> None:
    for raw, kind, key in MATCHES:
        result = match_url(raw)
        assert result is not None, f"expected match for {raw}"
        enricher, matched_key = result
        assert enricher.kind == kind, f"{raw}: expected kind {kind}, got {enricher.kind}"
        assert matched_key == key, f"{raw}: expected key {key!r}, got {matched_key!r}"

    for raw in NON_MATCHES:
        result = match_url(raw)
        assert result is None, f"expected NO match for {raw}, got {result}"

    links = extract_links(
        '<p>see <a href="https://github.com/a/b" rel="noopener">repo</a> and '
        '<a href="https://github.com/a/b">again</a> plus '
        '<a href="https://arxiv.org/abs/1706.03762">paper</a></p>'
    )
    assert links == ["https://github.com/a/b", "https://arxiv.org/abs/1706.03762"], links
    assert extract_links("") == []

    print(f"OK: {len(MATCHES)} matches, {len(NON_MATCHES)} non-matches, link extraction")


if __name__ == "__main__":
    main()
