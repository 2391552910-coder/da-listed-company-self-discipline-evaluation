"""Mechanical citation checks, deliberately separate from semantic judgement.

Correct JSON, an existing source and a verbatim quotation cannot establish that
the source entails a claim, that its factual assertion is true, or that a law
applies. ``support_status`` always remains the model's unreviewed judgement.
"""
from copy import deepcopy
import json
import re

from app.documents.common import sha


SUPPORT = {"supported", "partial", "unsupported", "conflicted", "unknown"}
_ROOT_FIELDS = {"answer", "evidence_status", "limitations", "claims"}
_CLAIM_FIELDS = {"id", "text", "support_status", "citations"}
_CITATION_FIELDS = {"evidence_id", "quote"}
_ROLES = {"A": "annual_report", "R": "regulation"}


def _string(value, maximum, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _not_finite(value):
    raise ValueError("non-finite JSON value")


def _source_issue(record):
    if not isinstance(record, dict):
        return "INVALID_SOURCE_RECORD"
    evidence_id = record.get("evidence_id")
    if not isinstance(evidence_id, str) or not re.fullmatch(r"[AR][1-9][0-9]*", evidence_id):
        return "INVALID_SOURCE_ID"
    if record.get("source_role") != _ROLES[evidence_id[0]]:
        return "SOURCE_ROLE_MISMATCH"
    if record.get("corpus_type", record["source_role"]) != record["source_role"]:
        return "SOURCE_ROLE_MISMATCH"
    content = record.get("content")
    if not _string(content, 1000000):
        return "INVALID_SOURCE_CONTENT"
    if record.get("content_sha256") != sha(content.encode("utf-8")):
        return "SOURCE_CONTENT_HASH_MISMATCH"
    if any(not _string(record.get(key), 200) for key in ("chunk_id", "version_id")):
        return "INVALID_SOURCE_IDENTITY"
    start, end = record.get("char_start"), record.get("char_end")
    if type(start) is not int or type(end) is not int or start < 0 or end - start != len(content):
        return "INVALID_SOURCE_LOCATOR"
    kind, page = record.get("source_format"), record.get("physical_page")
    if not isinstance(kind, str) or kind not in {"pdf", "txt", "docx"}:
        return "INVALID_SOURCE_LOCATOR"
    if page is not None and (kind != "pdf" or type(page) is not int or page < 1):
        return "INVALID_SOURCE_LOCATOR"
    return None


def validate_output(text, source_records, source_validator=None):
    """Return a safe result with explicit validity, even for malformed model JSON.

    ``source_validator(record)`` is an optional trusted source-integrity boundary.
    It must raise or return ``False`` on mismatch. Returning normally certifies
    only that original-file/frame identity matches the frozen source record.
    Without it, quotation matching is confined to the frozen context snapshot.
    No supplied field can turn on semantic or human verification.
    """
    errors = []
    warnings = []
    def fail(code, path, message):
        errors.append({"code": code, "path": path, "message": message})

    result = {"answer_text": None, "model_evidence_status": None,
              "evidence_status": "insufficient", "limitations": [], "claims": [],
              "semantic_support_verified": False, "human_review_status": "not_reviewed"}
    parsed = None
    if not _string(text, 200000):
        fail("INVALID_MODEL_OUTPUT", "$", "模型输出须为不超过200000字符的有效文本")
    else:
        try:
            parsed = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_not_finite)
        except (ValueError, TypeError, RecursionError):
            fail("INVALID_JSON", "$", "模型输出不是严格JSON对象，或包含重复字段")
    if not isinstance(parsed, dict):
        if not errors:
            fail("INVALID_OUTPUT_OBJECT", "$", "模型输出根节点须为对象")
        parsed = {}
    elif set(parsed) != _ROOT_FIELDS:
        fail("INVALID_OUTPUT_FIELDS", "$", "模型输出字段须恰为answer、evidence_status、limitations、claims")

    answer = parsed.get("answer")
    if _string(answer, 30000):
        result["answer_text"] = answer
    else:
        fail("INVALID_ANSWER", "answer", "回答须为不超过30000字符的非空文本")
    model_status = parsed.get("evidence_status")
    if isinstance(model_status, str) and model_status in {"sufficient", "insufficient"}:
        result["model_evidence_status"] = model_status
    else:
        fail("INVALID_EVIDENCE_STATUS", "evidence_status", "证据状态须为sufficient或insufficient")
    limits = parsed.get("limitations")
    if isinstance(limits, list) and len(limits) <= 40 and all(_string(item, 4000) for item in limits):
        result["limitations"] = deepcopy(limits)
    else:
        fail("INVALID_LIMITATIONS", "limitations", "限制须为不超过40条的非空文本列表")

    sources = {}
    source_issues = {}
    if not isinstance(source_records, list):
        fail("INVALID_SOURCE_RECORDS", "sources", "最终上下文来源记录须为列表")
        source_records = []
    for position, record in enumerate(source_records):
        issue = _source_issue(record)
        evidence_id = record.get("evidence_id") if isinstance(record, dict) else None
        if issue:
            fail(issue, "sources[" + str(position) + "]", "冻结来源记录的身份、内容哈希或定位无效")
            if isinstance(evidence_id, str):
                source_issues[evidence_id] = issue
            continue
        if evidence_id in sources:
            source_issues[evidence_id] = "DUPLICATE_SOURCE_ID"
            fail("DUPLICATE_SOURCE_ID", "sources[" + str(position) + "]", "最终上下文存在重复证据标识")
        else:
            sources[evidence_id] = deepcopy(record)
    integrity = {}
    citation_count = valid_count = 0
    cited_roles = set()
    cited_ids = set()
    claim_ids = set()
    claims = parsed.get("claims")
    if not isinstance(claims, list) or len(claims) > 80:
        fail("INVALID_CLAIMS", "claims", "论断须为不超过80项的列表")
        claims = []
    for index, claim in enumerate(claims):
        path = "claims[" + str(index) + "]"
        if not isinstance(claim, dict) or set(claim) != _CLAIM_FIELDS:
            fail("INVALID_CLAIM_FIELDS", path, "论断字段须恰为id、text、support_status、citations")
            if not isinstance(claim, dict):
                continue
        identifier = claim.get("id")
        if not _string(identifier, 64) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identifier):
            fail("INVALID_CLAIM_ID", path + ".id", "论断标识格式无效")
            identifier = None
        elif identifier in claim_ids:
            fail("DUPLICATE_CLAIM_ID", path + ".id", "论断标识重复")
        else:
            claim_ids.add(identifier)
        claim_text = claim.get("text")
        if not _string(claim_text, 10000):
            fail("INVALID_CLAIM_TEXT", path + ".text", "论断须为不超过10000字符的非空文本")
            claim_text = None
        support = claim.get("support_status")
        if not isinstance(support, str) or support not in SUPPORT:
            fail("INVALID_SUPPORT_STATUS", path + ".support_status", "论断支持状态无效")
            support = "unknown"
        item = {"id": identifier, "text": claim_text, "support_status": support,
                "support_status_source": "model", "semantic_support_verified": False,
                "human_review_status": "not_reviewed", "citations": []}
        citations = claim.get("citations")
        if not isinstance(citations, list) or len(citations) > 40:
            fail("INVALID_CITATIONS", path + ".citations", "引用须为不超过40项的列表")
            citations = []
        claim_valid = 0
        for number, citation in enumerate(citations):
            citation_count += 1
            where = path + ".citations[" + str(number) + "]"
            citation_errors = []
            def bad(code, message):
                citation_errors.append(code)
                fail(code, where, message)
            if not isinstance(citation, dict) or set(citation) != _CITATION_FIELDS:
                bad("INVALID_CITATION_FIELDS", "引用字段须恰为evidence_id与quote")
                if not isinstance(citation, dict):
                    item["citations"].append({"evidence_id": None, "quote": None,
                        "mechanical_status": "invalid", "errors": citation_errors,
                        "semantic_support_verified": False, "human_review_status": "not_reviewed"})
                    continue
            eid, quote = citation.get("evidence_id"), citation.get("quote")
            if not isinstance(eid, str) or not re.fullmatch(r"[AR][1-9][0-9]*", eid):
                bad("INVALID_EVIDENCE_ID", "引用标识须为最终上下文中的A或R编号")
                eid = None
            record = sources.get(eid)
            if record is None:
                bad("UNKNOWN_EVIDENCE_ID", "引用标识不属于最终上下文")
            elif eid in source_issues:
                bad(source_issues[eid], "该编号的冻结来源记录无效或有歧义")
            if not _string(quote, 12000):
                bad("INVALID_QUOTE", "引文须为不超过12000字符的非空文本")
                quote = None
            offset = record["content"].find(quote) if record and quote is not None else -1
            if record and quote is not None and offset < 0:
                bad("QUOTE_NOT_VERBATIM", "引文不是所引最终来源片段的逐字子串")
            if record and eid not in source_issues and source_validator is not None and eid not in integrity:
                try:
                    checked = source_validator(deepcopy(record))
                    integrity[eid] = "invalid" if checked is False else "verified"
                except Exception:
                    integrity[eid] = "invalid"
            integrity_status = integrity.get(eid, "not_checked")
            if integrity_status == "invalid":
                bad("SOURCE_INTEGRITY_FAILED", "原始来源校验失败，不能将冻结引文视为已核对原件")
            checked_citation = {"evidence_id": eid, "quote": quote,
                "mechanical_status": "invalid" if citation_errors else "valid",
                "source_integrity_status": integrity_status,
                "exact_quote_match": bool(record and quote is not None and offset >= 0),
                "errors": citation_errors, "semantic_support_verified": False,
                "human_review_status": "not_reviewed", "source": deepcopy(record)}
            if record and quote is not None and offset >= 0:
                checked_citation.update(quote_char_start=record["char_start"] + offset,
                                        quote_char_end=record["char_start"] + offset + len(quote))
            if not citation_errors:
                claim_valid += 1
                valid_count += 1
                cited_roles.add(record["source_role"])
                cited_ids.add(eid)
            item["citations"].append(checked_citation)
        if support in {"supported", "partial", "conflicted"} and not claim_valid:
            fail("SUPPORT_WITHOUT_VALID_CITATION", path, "此模型支持判断没有可机械核对的引用")
        item["mechanical_citation_status"] = "valid" if claim_valid == len(citations) and claim_valid else "invalid" if citations else "none"
        result["claims"].append(item)

    # The answer prose may omit bracket markers, but any marker it does present
    # must be grounded in the same final context and in a mechanically valid
    # structured citation. A valid claims section must not conceal an invented
    # inline [A99]/[R99] reference in the displayed narrative.
    body_ids = list(dict.fromkeys(re.findall(r"\[([AR][0-9]+)\]", result["answer_text"] or "")))
    unmatched_body_ids = []
    for evidence_id in body_ids:
        if evidence_id not in sources or evidence_id in source_issues:
            fail("UNKNOWN_BODY_EVIDENCE_ID", "answer", "正文引用编号不属于可用的最终上下文：" + evidence_id)
            unmatched_body_ids.append(evidence_id)
        elif evidence_id not in cited_ids:
            fail("BODY_CITATION_WITHOUT_VALID_CLAIM", "answer", "正文引用没有对应的有效论断引用：" + evidence_id)
            unmatched_body_ids.append(evidence_id)

    missing_roles = [role for role in ("annual_report", "regulation") if role not in cited_roles]
    if model_status == "sufficient" and missing_roles:
        warnings.append({"code": "MISSING_CITED_SOURCE_ROLE", "missing_roles": missing_roles,
                         "message": "模型声称证据充分，但有效引用未覆盖年报与法规两类来源；结果按证据不足展示"})
    if not result["claims"] and model_status == "sufficient":
        warnings.append({"code": "NO_CHECKABLE_CLAIMS", "message": "模型声称证据充分，但没有可核对论断"})
    if model_status == "sufficient" and not errors and not missing_roles and result["claims"]:
        result["evidence_status"] = "sufficient"
    result["validation"] = {
        "status": "invalid" if errors else "valid", "errors": errors, "warnings": warnings,
        "citation_count": citation_count, "mechanical_citation_valid_count": valid_count,
        "missing_cited_roles": missing_roles,
        "body_evidence_ids": body_ids, "body_unmatched_evidence_ids": unmatched_body_ids,
        "mechanical_evidence_status": "dual_source_cited" if not missing_roles else "single_source_cited" if cited_roles else "no_valid_citations",
        "uncited_context_evidence_ids": sorted(set(sources) - cited_ids),
        "source_integrity_checked": source_validator is not None,
        "semantic_support_verified": False, "human_review_status": "not_reviewed",
        "limitations": ["逐字引文、来源身份及定位校验不构成语义支持、事实真实性或法律适用性证明；支持状态为模型判断，尚未经人工复核。"],
    }
    return result
