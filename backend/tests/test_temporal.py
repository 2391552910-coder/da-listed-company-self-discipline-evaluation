import json
import pytest

from app.documents.common import DomainError
from app.retrieval.temporal import select_regulations


def regulation(chunk_id="rule-1", **metadata):
    return {"id": chunk_id, "corpus_type": "regulation", "report_year": 2024,
            "metadata_json": json.dumps(metadata)}


def select(rows, **kwargs):
    return select_regulations(rows, temporal_filter=True, target_date="2024-01-01",
                              target_date_role="event_date", **kwargs)


def test_disabled_filter_is_explicit_and_metadata_opaque():
    rows = [regulation(effective_from="unverified invalid declaration")]
    selected, audit = select_regulations(rows)
    assert selected == rows and selected is not rows
    assert audit["records"][0]["reason"] == "filter_disabled"
    assert audit["semantic_applicability_verified"] is False


@pytest.mark.parametrize("kwargs", [
    {"temporal_filter": 1}, {"temporal_filter": "false"}, {"unknown_policy": False},
    {"unknown_policy": "guess"}, {"target_date": "2024-01-01"},
    {"target_date_role": "event_date"}, {"unknown_policy": "exclude"},
])
def test_invalid_disabled_requests(kwargs):
    with pytest.raises(DomainError) as caught:
        select_regulations([], **kwargs)
    assert caught.value.status == 422


@pytest.mark.parametrize("kwargs", [
    {}, {"target_date": True}, {"target_date": "20240101"}, {"target_date": "2024-02-30"},
    {"target_date": "2024-01-01", "target_date_role": "report_year"},
    {"target_date": "2024-01-01", "target_date_role": True},
])
def test_enabled_filter_requires_explicit_valid_date_and_role(kwargs):
    with pytest.raises(DomainError) as caught:
        select_regulations([], temporal_filter=True, **kwargs)
    assert caught.value.status == 422


def test_half_open_interval_includes_start_excludes_end():
    start = regulation("start", effective_from="2024-01-01", effective_to_exclusive="2025-01-01")
    end = regulation("end", effective_from="2023-01-01", effective_to_exclusive="2024-01-01")
    future = regulation("future", effective_from="2024-01-02", effective_to_exclusive="2026-01-01")
    selected, audit = select([start, end, future])
    assert selected == [start]
    assert [r["decision"] for r in audit["records"]] == ["retain", "exclude", "exclude"]
    assert audit["records"][1]["reason"] == "at_or_after_effective_to_exclusive"
    assert all(r["semantic_applicability_verified"] is False for r in audit["records"])


def test_missing_end_is_unknown_not_infinite():
    unknown = regulation("unknown", effective_from="2023-01-01")
    unbounded = regulation("unbounded", effective_from="2023-01-01", interval_end_status="unbounded")
    selected, audit = select([unknown, unbounded], unknown_policy="exclude")
    assert selected == [unbounded]
    assert audit["unknown_count"] == 1
    assert audit["records"][0]["reason"] == "unknown_end"
    assert audit["records"][1]["reason"] == "within_declared_interval"


def test_unknown_boundaries_follow_policy_and_never_infer_report_year():
    row = regulation()
    retained, audit = select([row], unknown_policy="retain")
    assert retained == [row] and audit["records"][0]["reason"] == "unknown_start_and_end"
    excluded, _ = select([row], unknown_policy="exclude")
    assert excluded == []


def test_known_outside_boundary_wins_over_unknown_other_boundary():
    before = regulation("before", effective_from="2025-01-01")
    after = regulation("after", effective_to_exclusive="2023-12-31")
    selected, audit = select([before, after], unknown_policy="retain")
    assert selected == []
    assert audit["excluded_count"] == 2 and audit["unknown_count"] == 0


@pytest.mark.parametrize("metadata", [
    {"effective_from": "2024-13-01"}, {"effective_from": 20240101},
    {"effective_from": ""}, {"effective_to_exclusive": True},
    {"effective_from": "2024-01-01", "effective_to_exclusive": "2024-01-01"},
    {"effective_from": "2025-01-01", "effective_to_exclusive": "2024-01-01"},
    {"interval_end_status": "infinite"},
    {"interval_end_status": "unbounded", "effective_to_exclusive": "2025-01-01"},
    {"interval_end_status": "known_end"},
])
def test_malformed_or_conflicting_metadata_is_not_guessed(metadata):
    with pytest.raises(DomainError) as caught:
        select([regulation(**metadata)])
    assert caught.value.status == 409
    assert caught.value.code == "INVALID_TEMPORAL_METADATA"


@pytest.mark.parametrize("raw", ["[]", "null", "{bad", {}, True])
def test_metadata_must_be_object_json(raw):
    row = regulation()
    row["metadata_json"] = raw
    with pytest.raises(DomainError):
        select([row])


def test_empty_selection_stays_empty():
    selected, audit = select([])
    assert selected == [] and audit["retained_count"] == 0
    assert audit["records"] == []


@pytest.mark.parametrize("rows", [
    (), [None], [{"id": "x", "corpus_type": "annual_report"}],
    [{"id": True, "corpus_type": "regulation"}],
    [regulation("duplicate"), regulation("duplicate")],
])
def test_source_scope_and_ids_validated(rows):
    with pytest.raises(DomainError) as caught:
        select(rows)
    assert caught.value.status == 422


def test_evaluation_date_role_is_preserved():
    selected, audit = select_regulations(
        [regulation(effective_from="2023-01-01", interval_end_status="unbounded")],
        temporal_filter=True, target_date="2024-01-01", target_date_role="evaluation_date")
    assert selected and audit["target_date_role"] == "evaluation_date"
