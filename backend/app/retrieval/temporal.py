"""Conditional interval filtering over declared regulation metadata.

No report year, publication date, filename or current time supplies missing event
or effective dates. Keeping a row means only that the declared interval permits
it under the chosen policy; it is not a legal-applicability certification.
"""
from __future__ import annotations

from datetime import date
import json
import re

from app.documents.common import DomainError


def _date(value, *, field, metadata=False):
    status = 409 if metadata else 422
    code = "INVALID_TEMPORAL_METADATA" if metadata else "INVALID_TARGET_DATE"
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise DomainError(code, f"{field} must be an explicit ISO YYYY-MM-DD date", status)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DomainError(code, f"{field} is not a valid calendar date", status)


def _metadata(row):
    raw = row.get("metadata_json", "{}")
    if not isinstance(raw, str):
        raise DomainError("INVALID_TEMPORAL_METADATA", "Regulation metadata_json must be a JSON object string", 409)
    try:
        metadata = json.loads(raw)
    except (ValueError, TypeError):
        raise DomainError("INVALID_TEMPORAL_METADATA", "Regulation metadata_json is not valid JSON", 409)
    if not isinstance(metadata, dict):
        raise DomainError("INVALID_TEMPORAL_METADATA", "Regulation metadata_json must contain an object", 409)
    start_value = metadata.get("effective_from")
    end_value = metadata.get("effective_to_exclusive")
    # Only JSON null/absent means unknown. Empty or malformed strings are errors.
    start = None if start_value is None else _date(start_value, field="effective_from", metadata=True)
    end = None if end_value is None else _date(end_value, field="effective_to_exclusive", metadata=True)
    end_status = metadata.get("interval_end_status", "unknown")
    if end_status is None:
        end_status = "unknown"
    if not isinstance(end_status, str) or end_status not in {"unknown", "known_end", "unbounded"}:
        raise DomainError("INVALID_TEMPORAL_METADATA", "interval_end_status must be unknown, known_end or unbounded", 409)
    if start is not None and end is not None and end <= start:
        raise DomainError("INVALID_TEMPORAL_METADATA", "Effective interval must have a strictly later exclusive end", 409)
    if end_status == "unbounded" and end is not None:
        raise DomainError("INVALID_TEMPORAL_METADATA", "An unbounded interval cannot also declare an end date", 409)
    if end_status == "known_end" and end is None:
        raise DomainError("INVALID_TEMPORAL_METADATA", "known_end requires effective_to_exclusive", 409)
    return start, end, end_status


def select_regulations(
    rows: list[dict],
    *,
    temporal_filter: bool = False,
    target_date: str | None = None,
    target_date_role: str | None = None,
    unknown_policy: str = "retain",
) -> tuple[list[dict], dict]:
    """Select rows by an explicitly requested date and declared half-open interval.

    An end date is exclusive. An absent end remains unknown unless the status is
    explicitly 'unbounded'. A known out-of-range boundary always excludes a row,
    even when its other boundary is unknown. Unknown rows otherwise follow the
    requested retain/exclude policy. Disabled filtering leaves metadata opaque.
    """
    if type(temporal_filter) is not bool:
        raise DomainError("INVALID_TEMPORAL_POLICY", "temporal_filter must be an explicit boolean", 422)
    if not isinstance(unknown_policy, str) or unknown_policy not in {"retain", "exclude"}:
        raise DomainError("INVALID_TEMPORAL_POLICY", "unknown_policy must be retain or exclude", 422)
    if not isinstance(rows, list):
        raise DomainError("INVALID_TEMPORAL_ROWS", "Regulation rows must be a list", 422)
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("corpus_type") != "regulation":
            raise DomainError("INVALID_TEMPORAL_ROWS", "Temporal selection accepts only regulation rows", 422)
        chunk_id = row.get("chunk_id", row.get("id"))
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in ids:
            raise DomainError("INVALID_TEMPORAL_ROWS", "Regulation chunk IDs must be nonempty and unique", 422)
        ids.add(chunk_id)

    if not temporal_filter:
        if target_date is not None or target_date_role is not None or unknown_policy != "retain":
            raise DomainError("TEMPORAL_FILTER_REQUIRED", "Date and nondefault policy require temporal_filter=true", 422)
        target = None
    else:
        if target_date is None:
            raise DomainError("TARGET_DATE_REQUIRED", "Explicit target_date is required; report_year is not an event date", 422)
        target = _date(target_date, field="target_date")
        if not isinstance(target_date_role, str) or target_date_role not in {"event_date", "evaluation_date"}:
            raise DomainError("INVALID_TARGET_DATE_ROLE", "target_date_role must be event_date or evaluation_date", 422)

    retained, records = [], []
    unknown_count = 0
    for row in rows:
        chunk_id = row.get("chunk_id", row.get("id"))
        record = {"chunk_id": chunk_id, "semantic_applicability_verified": False}
        if target is None:
            decision, reason, relation = "retain", "filter_disabled", "not_checked"
        else:
            start, end, end_status = _metadata(row)
            record.update(
                effective_from=start.isoformat() if start else None,
                effective_to_exclusive=end.isoformat() if end else None,
                interval_end_status=end_status,
            )
            if start is not None and target < start:
                decision, reason, relation = "exclude", "before_effective_from", "outside_declared_interval"
            elif end is not None and target >= end:
                decision, reason, relation = "exclude", "at_or_after_effective_to_exclusive", "outside_declared_interval"
            else:
                unknown_start = start is None
                unknown_end = end is None and end_status != "unbounded"
                if unknown_start or unknown_end:
                    unknown_count += 1
                    decision = unknown_policy
                    reason = ("unknown_start_and_end" if unknown_start and unknown_end else
                              "unknown_start" if unknown_start else "unknown_end")
                    relation = "unknown_declared_interval"
                else:
                    decision, reason, relation = "retain", "within_declared_interval", "inside_declared_interval"
        record.update(decision=decision, reason=reason, interval_relation=relation)
        records.append(record)
        if decision == "retain":
            retained.append(row)

    audit = {
        "schema_version": "declared-regulation-interval-selection-v1",
        "enabled": temporal_filter,
        "target_date": target.isoformat() if target else None,
        "target_date_role": target_date_role,
        "unknown_policy": unknown_policy,
        "input_count": len(rows),
        "retained_count": len(retained),
        "excluded_count": len(rows) - len(retained),
        "unknown_count": unknown_count,
        "records": records,
        "semantic_applicability_verified": False,
        "time_metadata_is_declared_not_independently_verified": True,
    }
    return retained, audit
