"""Whole-chunk dual-source context assembly with an externally frozen counter.

Source coordinates are supplied by the verified retrieval boundary. This module
validates their shape and consistency, preserves the source text verbatim, and
never claims that an original source semantically supports a generated answer.
"""
from copy import deepcopy
import json
from itertools import zip_longest
from numbers import Integral

from app.documents.common import DomainError, dump, sha

ROLES = ("annual_report", "regulation")
PREFIXES = {"annual_report": "A", "regulation": "R"}
SCHEMA = "dual-source-context-v1"


def _invalid(code, message):
    raise DomainError(code, message, 422)


def _json_copy(value, description):
    try:
        # JSON round-trip validation rejects NaN, infinity, unsupported objects,
        # and makes the record hashes independent of accidental Python aliases.
        encoded = dump(value)
        encoded.encode("utf-8")
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as error:
        raise DomainError("INVALID_CONTEXT_INPUT", description, 422) from error


def _count(counter, text):
    try:
        value = counter(text)
    except Exception as error:
        raise DomainError("TOKEN_COUNTER_FAILED", "Reference counter failed", 422) from error
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        _invalid("INVALID_TOKEN_COUNT", "Reference counter must return a non-negative integer")
    return int(value)


def _hit(hit, role):
    if not isinstance(hit, dict):
        _invalid("INVALID_EVIDENCE", "Each evidence candidate must be an object")
    row = _json_copy(hit, "Evidence candidate is not finite JSON data")
    for field in ("chunk_id", "content", "title", "version_id", "source_format"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            _invalid("INVALID_EVIDENCE", field + " must be a non-empty string")
    if row["source_format"] not in {"pdf", "txt", "docx"}:
        _invalid("INVALID_SOURCE_FORMAT", "Evidence format must be pdf, txt or docx")
    start, end = row.get("char_start"), row.get("char_end")
    if (isinstance(start, bool) or isinstance(end, bool) or
            not isinstance(start, Integral) or not isinstance(end, Integral) or
            start < 0 or end <= start or end - start != len(row["content"])):
        _invalid("INVALID_LOCATOR", "Character interval must exactly match the complete source text")
    row["char_start"], row["char_end"] = int(start), int(end)
    page = row.get("physical_page")
    if page is not None:
        if isinstance(page, bool) or not isinstance(page, Integral) or page < 1:
            _invalid("INVALID_LOCATOR", "Physical PDF pages start at one; unknown pages use null")
        if row["source_format"] != "pdf":
            _invalid("INVALID_LOCATOR", "TXT and DOCX evidence must use character intervals, not invented pages")
        row["physical_page"] = int(page)
    else:
        row["physical_page"] = None
    metadata = row.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        _invalid("INVALID_EVIDENCE", "Evidence metadata must be an object")
    warnings = row.setdefault("parse_warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        _invalid("INVALID_EVIDENCE", "Parse warnings must be a list of strings")
    for explicit_role in (row.get("corpus_type"), row.get("source_role"), metadata.get("corpus_type")):
        if explicit_role is not None and explicit_role != role:
            _invalid("SOURCE_ROLE_CONFLICT", "Evidence role conflicts with its corpus")
    return row


def _identity(hit):
    """Scores may differ across duplicate channel hits; provenance may not."""
    return dump({key: hit[key] for key in (
        "chunk_id", "content", "title", "version_id", "source_format",
        "physical_page", "char_start", "char_end", "metadata", "parse_warnings",
    )})


def _render(row, role, label):
    location = ("PDF physical page " + str(row["physical_page"])
                if row["physical_page"] is not None else
                "PDF physical page unknown" if row["source_format"] == "pdf" else
                row["source_format"].upper() + " body text")
    location += "; character interval [" + str(row["char_start"]) + "," + str(row["char_end"]) + ")"
    # JSON scalar formatting keeps newlines in titles and metadata from creating
    # extra apparent provenance headers. The original content stays untouched.
    identity = row["metadata"].get("identity_status",
                                  row["metadata"].get("source_identity_status", "unknown"))
    lines = [
        "[" + label + "] " + role,
        "Title: " + dump(row["title"]),
        "Source version: " + dump(row["version_id"]),
        "Location: " + location,
        "Registered source identity: " + dump(identity),
    ]
    if role == "regulation":
        for key in ("version_id", "publication_date", "effective_from", "effective_to_exclusive",
                    "interval_end_status", "metadata_as_of_date", "applicability_status"):
            lines.append("Declared " + key + ": " + dump(row["metadata"].get(key)))
        lines.append("Declared dates and versions do not certify legal applicability.")
    if row["parse_warnings"]:
        lines.append("Parse limitations: " + dump(row["parse_warnings"]))
    lines.extend(["Source text:", row["content"]])
    return "\n".join(lines)


def assemble_context(candidates, max_tokens, counter, tokenizer_identity):
    """Return a frozen, source-addressable context and explicit omissions.

    Candidates alternate annual-report first, then regulation, preserving each
    source's own ranking. A long block can be omitted while later short blocks
    still fit. Every trial counts the complete rendered context; counts are not
    assumed additive. Labels are assigned only to admitted chunks.
    """
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, Integral) or max_tokens < 1:
        _invalid("INVALID_CONTEXT_BUDGET", "Context budget must be a positive integer")
    max_tokens = int(max_tokens)
    if not callable(counter):
        _invalid("INVALID_TOKEN_COUNTER", "Reference counter must be callable")
    if not isinstance(tokenizer_identity, dict) or not tokenizer_identity:
        _invalid("INVALID_TOKENIZER_IDENTITY", "A non-empty frozen tokenizer identity is required")
    tokenizer_identity = _json_copy(tokenizer_identity, "Tokenizer identity must be finite JSON data")
    if not isinstance(candidates, dict) or set(candidates) - set(ROLES):
        _invalid("INVALID_CANDIDATE_ROLES", "Only annual_report and regulation corpora are accepted")
    initial_count = _count(counter, "")
    if initial_count > max_tokens:
        _invalid("BUDGET_TOO_SMALL", "Even the empty reference context exceeds the configured budget")

    pools = {}
    seen = {}
    omitted = []
    duplicate_count = 0
    input_counts = {}
    for role in ROLES:
        values = candidates.get(role, [])
        if not isinstance(values, list):
            _invalid("INVALID_CANDIDATES", "Each source corpus must contain a list")
        pools[role] = []
        input_counts[role] = len(values)
        for position, candidate in enumerate(values, 1):
            row = _hit(candidate, role)
            cid = row["chunk_id"]
            if cid in seen:
                previous_role, previous = seen[cid]
                if role != previous_role:
                    _invalid("SOURCE_ROLE_CONFLICT", "A chunk cannot appear in both source roles")
                if _identity(row) != _identity(previous):
                    _invalid("DUPLICATE_CHUNK_CONFLICT", "A duplicate chunk has inconsistent content or provenance")
                omitted.append({"chunk_id": cid, "source_role": role, "candidate_position": position,
                                "reason": "duplicate_chunk", "duplicate_of": cid})
                duplicate_count += 1
                continue
            seen[cid] = (role, row)
            pools[role].append((position, row))

    selected, blocks = [], []
    labels = {role: 0 for role in ROLES}
    count = initial_count
    budget_omissions = 0
    for pair in zip_longest(*(pools[role] for role in ROLES)):
        for role, entry in zip(ROLES, pair):
            if entry is None:
                continue
            position, row = entry
            label = PREFIXES[role] + str(labels[role] + 1)
            block = _render(row, role, label)
            trial = "\n\n".join([*blocks, block])
            trial_count = _count(counter, trial)
            if trial_count > max_tokens:
                omitted.append({"chunk_id": row["chunk_id"], "source_role": role,
                                "candidate_position": position, "reason": "context_budget",
                                "candidate_tokens": trial_count})
                budget_omissions += 1
                continue
            labels[role] += 1
            blocks.append(block)
            count = trial_count
            record = deepcopy(row)
            record.update(evidence_id=label, source_role=role, corpus_type=role,
                          candidate_position=position, content_sha256=sha(row["content"].encode("utf-8")),
                          semantic_support_verified=False)
            selected.append(record)

    text = "\n\n".join(blocks)
    # Detect a non-deterministic or stateful counter before publishing a snapshot.
    final_count = _count(counter, text)
    if final_count != count:
        _invalid("NONDETERMINISTIC_TOKEN_COUNTER", "Reference count changed for the same final context")
    if final_count > max_tokens:
        _invalid("CONTEXT_BUDGET_EXCEEDED", "Final context exceeds its reference budget")
    selected_roles = {row["source_role"] for row in selected}
    unique_count = sum(len(pool) for pool in pools.values())
    return {
        "schema_version": SCHEMA,
        "context_text": text,
        "context_sha256": sha(text.encode("utf-8")),
        "reference_token_count": final_count,
        "tokenizer_identity": tokenizer_identity,
        "source_records": selected,
        "source_records_sha256": sha(dump(selected).encode("utf-8")),
        "omitted_records": omitted,
        "diagnostics": {
            "input_candidate_counts": input_counts,
            "unique_candidate_counts": {role: len(pools[role]) for role in ROLES},
            "selected_counts": labels,
            "duplicate_count": duplicate_count,
            "missing_candidate_roles": [role for role in ROLES if not pools[role]],
            "missing_context_roles": [role for role in ROLES if role not in selected_roles],
            "max_reference_tokens": max_tokens,
            "remaining_reference_tokens": max_tokens - final_count,
            "has_budget_omissions": bool(budget_omissions),
            "budget_exhausted": final_count == max_tokens,
            "budget_excluded_all": unique_count > 0 and not selected and bool(budget_omissions),
            "semantic_support_verified": False,
        },
    }
