"""Closed Agent protocol fixtures; never invokes a model or tool."""
from copy import deepcopy
import json

import pytest

from app.documents.common import DomainError, dump, sha
from app.evaluation.agent_protocol import (
    make_plan_prompt, make_verify_prompt, parse_plan, parse_verification, protocol_snapshot,
)


def plan(**change):
    value = {"action": "retrieve", "query": "审计委员会履职", "source_role": "both", "reason": "补查履职证据"}
    value.update(change)
    return value


def review(**change):
    value = {"decision": "accept", "reason": "仅模型自检，尚未人工确认", "issues": []}
    value.update(change)
    return value


def context(**change):
    value = {"context_text": "[A1] 年报\n公司披露设有审计委员会。", "reference_token_count": 20,
             "tokenizer_identity": {"budget_unit": "reference_tokens"},
             "diagnostics": {"missing_context_roles": ["regulation"]}}
    value.update(change)
    return value


def test_plan_role_limits_one_query_and_stop_has_no_executable_fields():
    assert parse_plan(dump(plan(query="  补查问题  ")))["query"] == "补查问题"
    for role in ("annual_report", "regulation", "both"):
        assert parse_plan(dump(plan(source_role=role)))["source_role"] == role
    stopped = {"action": "stop", "query": None, "source_role": None, "reason": "资料不足且剩余次数为零"}
    assert parse_plan(dump(stopped)) == stopped


@pytest.mark.parametrize("changes", [
    {"action": True}, {"action": []}, {"action": "browse"}, {"source_role": "web"},
    {"source_role": []}, {"source_role": True}, {"query": ["a", "b"]},
    {"query": " "}, {"query": True}, {"query": "x" * 4001}, {"reason": ""},
    {"reason": False}, {"reason": "x" * 4001}, {"tool": "open_file"},
    {"action": "stop", "query": "must be null", "source_role": None},
    {"action": "stop", "query": None, "source_role": "both"},
    {"query": "\ud800"}, {"reason": "\udfff"},
])
def test_plan_rejects_unbounded_or_unrequested_authority(changes):
    text = json.dumps(plan(**changes), ensure_ascii=True)
    with pytest.raises(DomainError) as error:
        parse_plan(text)
    assert error.value.code == "AGENT_PLAN_INVALID" and error.value.status == 502


@pytest.mark.parametrize("parser,error_code", [
    (parse_plan, "AGENT_PLAN_INVALID"), (parse_verification, "AGENT_VERIFICATION_INVALID"),
])
@pytest.mark.parametrize("text", [
    "", "[]", "null", "true", "1", "{", "```json\n{}\n```", "prefix {} suffix",
    '{"action":"stop","action":"retrieve"}', '{"x":NaN}', '{"x":Infinity}',
    "x" * 30001, "\ud800",
])
def test_both_parsers_reject_non_strict_json_without_exposing_input(parser, error_code, text):
    with pytest.raises(DomainError) as error:
        parser(text)
    assert error.value.code == error_code
    assert "prefix" not in str(error.value)


def test_model_verification_cannot_upgrade_human_or_semantic_authority():
    result = parse_verification(dump(review()))
    assert result == review() and "semantic_support_verified" not in result
    assert parse_verification(dump(review(decision="revise", issues=["保留未知法规适用性"]))) == review(
        decision="revise", issues=["保留未知法规适用性"])
    for field in ("semantic_support_verified", "human_review_status", "score", "answer"):
        with pytest.raises(DomainError):
            parse_verification(dump(review(**{field: True})))


@pytest.mark.parametrize("changes", [
    {"decision": True}, {"decision": []}, {"decision": "verified"},
    {"reason": False}, {"reason": " "}, {"reason": "x" * 4001},
    {"issues": "none"}, {"issues": {}}, {"issues": [False]}, {"issues": [""]},
    {"issues": ["x"] * 21}, {"issues": ["x" * 4001]}, {"issues": ["\ud800"]},
    {"decision": "revise", "issues": []},
])
def test_verification_schema_is_closed_and_revision_needs_an_issue(changes):
    with pytest.raises(DomainError) as error:
        parse_verification(json.dumps(review(**changes), ensure_ascii=True))
    assert error.value.code == "AGENT_VERIFICATION_INVALID"


def test_prompt_uses_frozen_template_and_exact_context_without_mutation():
    template = protocol_snapshot()
    template["planning_system"] = "frozen planning text"
    ctx, history, remaining = context(), [{"action": "stop", "reason": "old"}], {"supplemental_calls": 1}
    before = deepcopy((template, ctx, history, remaining))
    prompt = make_plan_prompt({"text": "问题"}, ctx, history, remaining, template)
    assert prompt["messages"][0]["content"] == "frozen planning text"
    payload = json.loads(prompt["messages"][1]["content"])
    assert payload["final_context"] == ctx["context_text"]
    assert payload["remaining"] == remaining and payload["history"] == history
    assert prompt["messages_sha256"] == sha(dump(prompt["messages"]).encode())
    assert prompt["reference_token_count"] == 20 and prompt["complete_provider_prompt_tokens"] is None
    assert (template, ctx, history, remaining) == before
    template["verification_system"] = "frozen verification text"
    check = make_verify_prompt({"text": "问题"}, ctx, '{"answer":"raw fixture"}', template)
    assert check["messages"][0]["content"] == "frozen verification text"
    check_payload = json.loads(check["messages"][1]["content"])
    assert check_payload["draft"] == '{"answer":"raw fixture"}'
    assert check_payload["review_authority"] == "model_self_review_only"
    assert check["messages_sha256"] == sha(dump(check["messages"]).encode())


def test_snapshot_is_independent_and_materials_have_no_tool_authority():
    first = protocol_snapshot()
    first["planning_system"] = "mutated"
    assert protocol_snapshot()["planning_system"] != "mutated"
    assert protocol_snapshot()["planning_system"].startswith("[DALUN_AGENT_PLAN_V1]")
    assert protocol_snapshot()["verification_system"].startswith("[DALUN_AGENT_VERIFY_V1]")
    assert "无权更改" in protocol_snapshot()["planning_system"]
    assert "机械校验通过不代表语义支持" in protocol_snapshot()["verification_system"]
    assert "不是独立评审或人工确认" in protocol_snapshot()["verification_system"]


@pytest.mark.parametrize("change", [
    {"reference_token_count": True}, {"reference_token_count": -1},
    {"context_text": "\ud800"}, {"tokenizer_identity": {}}, {"tokenizer_identity": None},
])
def test_invalid_context_identity_cannot_be_labelled_a_valid_prompt(change):
    with pytest.raises(DomainError) as error:
        make_plan_prompt({"text": "问题"}, context(**change), [], {}, protocol_snapshot())
    assert error.value.code == "INVALID_AGENT_PROMPT"


def test_nonfinite_or_invalid_unicode_in_prompt_payload_is_rejected_cleanly():
    for history in ([float("nan")], ["\ud800"]):
        with pytest.raises(DomainError) as error:
            make_plan_prompt({"text": "问题"}, context(), history, {}, protocol_snapshot())
        assert error.value.code == "INVALID_AGENT_PROMPT"


@pytest.mark.parametrize("context_value", [None, [], "invalid"])
def test_non_mapping_context_gets_domain_error_for_both_prompt_kinds(context_value):
    for make in (
        lambda: make_plan_prompt({}, context_value, [], {}, protocol_snapshot()),
        lambda: make_verify_prompt({}, context_value, {}, protocol_snapshot()),
    ):
        with pytest.raises(DomainError) as error:
            make()
        assert error.value.code == "INVALID_AGENT_PROMPT"
