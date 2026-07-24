"""Versioned built-in browser-history exclusions.

Rules are intentionally structured and small. The extension mirrors this
contract and shared fixtures keep the two implementations synchronized.
"""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from .history_policy import NormalizedHistoryUrl

PathMatch = Literal["exact", "prefix"]


@dataclass(frozen=True)
class HistorySystemRule:
    id: str
    label: str
    description: str
    hosts: tuple[str, ...]
    path_match: PathMatch
    path: str

    def matches(self, normalized: NormalizedHistoryUrl) -> bool:
        if normalized.hostname not in self.hosts:
            return False
        candidate = urlsplit(normalized.url).path or "/"
        if self.path_match == "exact":
            return candidate == self.path
        return candidate == self.path or candidate.startswith(f"{self.path.rstrip('/')}/")


HISTORY_SYSTEM_POLICY_REVISION = 1
HISTORY_SYSTEM_RULES = (
    HistorySystemRule(
        id="google-home",
        label="Google homepage",
        description="Skip the Google search landing page.",
        hosts=("google.com", "www.google.com"),
        path_match="exact",
        path="/",
    ),
    HistorySystemRule(
        id="google-search",
        label="Google search results",
        description="Skip Google result pages while retaining other Google-hosted content.",
        hosts=("google.com", "www.google.com"),
        path_match="exact",
        path="/search",
    ),
    HistorySystemRule(
        id="bing-home",
        label="Bing homepage",
        description="Skip the Bing search landing page.",
        hosts=("bing.com", "www.bing.com"),
        path_match="exact",
        path="/",
    ),
    HistorySystemRule(
        id="bing-search",
        label="Bing search results",
        description="Skip Bing result pages.",
        hosts=("bing.com", "www.bing.com"),
        path_match="exact",
        path="/search",
    ),
    HistorySystemRule(
        id="duckduckgo-search",
        label="DuckDuckGo search",
        description="Skip DuckDuckGo landing and result pages.",
        hosts=("duckduckgo.com", "www.duckduckgo.com"),
        path_match="exact",
        path="/",
    ),
    HistorySystemRule(
        id="google-account",
        label="Google account screens",
        description="Skip Google sign-in and account chooser pages.",
        hosts=("accounts.google.com",),
        path_match="prefix",
        path="/",
    ),
    HistorySystemRule(
        id="microsoft-account",
        label="Microsoft account screens",
        description="Skip Microsoft sign-in and account chooser pages.",
        hosts=("login.microsoftonline.com",),
        path_match="prefix",
        path="/",
    ),
    HistorySystemRule(
        id="apple-account",
        label="Apple account screens",
        description="Skip Apple sign-in and account management pages.",
        hosts=("appleid.apple.com",),
        path_match="prefix",
        path="/",
    ),
    HistorySystemRule(
        id="github-login",
        label="GitHub sign-in",
        description="Skip the GitHub sign-in page without excluding GitHub content.",
        hosts=("github.com",),
        path_match="exact",
        path="/login",
    ),
)
HISTORY_SYSTEM_RULES_BY_ID = {rule.id: rule for rule in HISTORY_SYSTEM_RULES}


def matching_system_rule(
    normalized: NormalizedHistoryUrl,
    *,
    disabled_rule_ids: set[str],
) -> HistorySystemRule | None:
    return next(
        (
            rule
            for rule in HISTORY_SYSTEM_RULES
            if rule.id not in disabled_rule_ids and rule.matches(normalized)
        ),
        None,
    )
