import json
from pathlib import Path

from app.history_policy import validate_normalized_history_url
from app.history_system_policy import (
    HISTORY_SYSTEM_POLICY_REVISION,
    HISTORY_SYSTEM_RULES,
    matching_system_rule,
)

SHARED = Path(__file__).resolve().parents[2] / "shared"


def test_shared_system_policy_matches_backend_definitions():
    fixture = json.loads((SHARED / "history-system-policy-v1.json").read_text())

    assert fixture["revision"] == HISTORY_SYSTEM_POLICY_REVISION
    assert fixture["rules"] == [
        {
            "id": rule.id,
            "hosts": list(rule.hosts),
            "path_match": rule.path_match,
            "path": rule.path,
        }
        for rule in HISTORY_SYSTEM_RULES
    ]


def test_system_policy_excludes_exact_google_shapes_not_all_google_content():
    assert (
        matching_system_rule(
            validate_normalized_history_url("https://www.google.com/"),
            disabled_rule_ids=set(),
        ).id
        == "google-home"
    )
    assert (
        matching_system_rule(
            validate_normalized_history_url("https://www.google.com/search?q=news"),
            disabled_rule_ids=set(),
        ).id
        == "google-search"
    )
    assert (
        matching_system_rule(
            validate_normalized_history_url("https://docs.google.com/document/d/useful"),
            disabled_rule_ids=set(),
        )
        is None
    )


def test_system_policy_override_disables_only_the_selected_rule():
    normalized = validate_normalized_history_url("https://github.com/login")

    assert matching_system_rule(normalized, disabled_rule_ids=set()) is not None
    assert matching_system_rule(normalized, disabled_rule_ids={"github-login"}) is None
