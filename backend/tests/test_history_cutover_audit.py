from scripts.audit_history_search_cutover import compare_results


def test_cutover_audit_reports_legacy_coverage_without_exposing_rows():
    audit = compare_results(
        "private query",
        {(1, "a" * 64), (1, "b" * 64)},
        {(1, "b" * 64), (2, "c" * 64)},
    )

    assert audit.legacy_results == 2
    assert audit.document_results == 2
    assert audit.shared_results == 1
    assert audit.legacy_coverage == 0.5
    assert "a" * 64 not in repr(audit)
