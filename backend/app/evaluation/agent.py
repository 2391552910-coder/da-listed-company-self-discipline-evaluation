"""Bounded dual-source Agent execution over the existing durable call ledger.

Planning can only request a query against the frozen local index. Context IDs
may change while gathering evidence, then stay fixed for drafting, model review
and the optional single revision. Model review never certifies semantic support.
"""
from copy import deepcopy
import json
import time

from app.documents.common import DomainError, dump, sha
from app.evaluation.agent_protocol import make_plan_prompt, make_verify_prompt, parse_plan, parse_verification
from app.retrieval.context_builder import assemble_context

AGENT_DEFAULTS = {"max_rounds": 1, "max_supplemental_calls": 4, "allow_revision": True}


def normalize_agent_config(value=None):
    if value is not None and (not isinstance(value, dict) or set(value) - set(AGENT_DEFAULTS)):
        raise DomainError("INVALID_AGENT_CONFIG", "Agent配置包含未知字段或不是对象", 422)
    result = AGENT_DEFAULTS | (value or {})
    for key, low, high in (("max_rounds", 1, 3), ("max_supplemental_calls", 0, 12)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise DomainError("INVALID_AGENT_CONFIG", key + "不合法", 422)
    if type(result["allow_revision"]) is not bool:
        raise DomainError("INVALID_AGENT_CONFIG", "allow_revision须为布尔值", 422)
    return result


def validate_trace(trace):
    """Check internal hashes in addition to the enclosing immutable file hash."""
    try:
        if trace["schema_version"] != "dual-agent-trace-v1" or not isinstance(trace["steps"], list):
            raise ValueError()
        initial = trace.get("initial_context")
        if initial and (sha(initial["context_text"].encode()) != initial["context_sha256"] or
            sha(dump(initial["source_records"]).encode()) != initial["source_records_sha256"]):
            raise ValueError()
        keys = set()
        for step in trace["steps"]:
            if not isinstance(step["key"], str) or step["key"] in keys:
                raise ValueError()
            keys.add(step["key"])
            if step.get("prompt"):
                prompt = step["prompt"]
                if sha(dump(prompt["messages"]).encode()) != prompt["messages_sha256"]:
                    raise ValueError()
            for context in [step.get("context"), trace.get("initial_context")]:
                if context and (sha(context["context_text"].encode()) != context["context_sha256"] or
                    sha(dump(context["source_records"]).encode()) != context["source_records_sha256"]):
                    raise ValueError()
    except (KeyError, ValueError, TypeError, UnicodeError) as error:
        raise DomainError("AGENT_TRACE_MISMATCH", "Agent步骤或上下文哈希不一致", 409) from error


class AgentRunner:
    def __init__(self, service, run, answer, question, request, attempt,
                 job_id, worker_id, counter, identity, checkpoint, deadline):
        self.service, self.docs = service, service.docs
        self.run, self.answer, self.question, self.request = run, answer, question, request
        self.attempt, self.job_id, self.worker_id = attempt, job_id, worker_id
        self.counter, self.identity, self.checkpoint, self.deadline = counter, identity, checkpoint, deadline
        self.config = request["agent_config"]
        self.protocol = request["agent_protocol"]
        if "agent_trace" not in answer:
            answer["agent_trace"] = {"schema_version": "dual-agent-trace-v1",
                "protocol_version": self.protocol["version"], "steps": [],
                "initial_context": deepcopy(answer["context"]),
                "candidate_pools": {role: deepcopy(answer["retrieval"][role]) for role in ("annual_report", "regulation")},
                "supplemental_calls_used": 0, "stop_reason": None,
                "verification": None, "revised": False, "model_judgement_only": True,
                "semantic_support_verified": False, "human_review_status": "not_reviewed"}
            self._save()
        validate_trace(answer["agent_trace"])
        if answer["agent_trace"]["protocol_version"] != self.protocol["version"]:
            raise DomainError("AGENT_PROTOCOL_MISMATCH", "Agent协议与冻结运行不一致", 409)

    @property
    def trace(self):
        return self.answer["agent_trace"]

    def _save(self):
        # Keep the caller's object current even if a later stage raises. The
        # outer evaluator then preserves the complete interrupted trace.
        frozen = self.service._save_answer(self.answer, self.run["request_sha256"], self.job_id, self.worker_id)
        self.answer.clear()
        self.answer.update(frozen)

    def _steps(self, key):
        return next((step for step in self.trace["steps"] if step["key"] == key), None)

    def _budget(self):
        calls = self.service._calls(self.run["id"])
        usages = [json.loads(c["usage_json"]) for c in calls if c.get("usage_json")]
        known = [u["total_tokens"] for u in usages if u.get("status") == "reported"]
        used = self._supplemental_count()
        return {"model_calls_used": len(calls), "max_model_calls": self.request["budget"]["max_model_calls"],
            "observed_total_tokens": sum(known), "provider_usage_complete": len(usages) == len(calls) and all(u.get("status") == "reported" for u in usages),
            "max_observed_tokens": self.request["budget"]["max_observed_tokens"],
            "supplemental_calls_used": used, "max_supplemental_calls": self.config["max_supplemental_calls"],
            "remaining_runtime_seconds": max(0, round(self.deadline - time.monotonic(), 3))}

    def _supplemental_count(self):
        with self.docs.db.session() as db:
            rows = db.rows("SELECT * FROM t_evaluation_answer WHERE run_id=?", (self.run["id"],))
        count = 0
        for row in rows:
            stored = self.service._read_answer(row, self.run["request_sha256"])
            count += sum(s.get("kind") == "retrieval" for s in (stored.get("agent_trace") or {}).get("steps", []))
        return count

    def _model_step(self, key, kind, prompt):
        self.checkpoint("agent_" + kind, self.question["key"])
        step = self._steps(key)
        if step is None:
            step = {"key": key, "kind": kind, "status": "prepared", "prompt": deepcopy(prompt),
                    "context_sha256": self.answer["context"]["context_sha256"], "budget": self._budget()}
            self.trace["steps"].append(step)
            self._save()
            step = self._steps(key)
        prompt = step["prompt"]
        calls = [c for c in self.service._calls(self.run["id"])
                 if c["answer_id"] == self.answer["id"] and c.get("step_key") == key]
        if len(calls) > 1:
            raise DomainError("MODEL_CALL_MISMATCH", "Agent步骤对应多个模型调用", 409)
        wire = {"messages": prompt["messages"], "model_identity": self.request["model"],
                "max_output_tokens": self.request["budget"]["max_output_tokens"],
                "temperature": self.request["model"].get("temperature")}
        if calls:
            call = calls[0]
            if call["request_sha256"] != sha(dump(wire).encode()) or call.get("purpose") != "agent_" + kind:
                raise DomainError("MODEL_CALL_MISMATCH", "Agent调用与冻结步骤不一致", 409)
            if call["status"] != "succeeded":
                step.update(status="uncertain", model_call=self.service._call_public(call), budget=self._budget())
                self._save()
                raise DomainError("MODEL_CALL_OUTCOME_UNKNOWN", "Agent步骤已有结果未知调用，不自动重发", 409)
            response = self.service._response(call)
        else:
            call = self.service._reserve_call(self.run, self.answer, prompt, self.attempt,
                self.job_id, self.worker_id, purpose="agent_" + kind, step_key=key)
            try:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise DomainError("BUDGET_EXHAUSTED", "Agent运行时长预算已耗尽", 409)
                response = self.service.provider.complete(prompt["messages"], model=self.request["model"]["model"],
                    max_output_tokens=self.request["budget"]["max_output_tokens"],
                    temperature=self.request["model"].get("temperature"),
                    timeout_seconds=min(remaining, self.request["budget"]["request_timeout_seconds"]),
                    heartbeat=lambda: self.checkpoint("agent_" + kind, self.question["key"]))
            except DomainError as error:
                if error.code in {"TASK_CANCELLED", "LEASE_LOST"}:
                    raise
                failed = self.service._complete_call(call, None, self.job_id, self.worker_id, error.code)
                self._steps(key).update(status="uncertain", error_code=error.code,
                    model_call=self.service._call_public(failed), budget=self._budget())
                self._save()
                raise
            except Exception:
                failed = self.service._complete_call(call, None, self.job_id, self.worker_id, "MODEL_CALL_FAILED")
                self._steps(key).update(status="uncertain", error_code="MODEL_CALL_FAILED",
                    model_call=self.service._call_public(failed), budget=self._budget())
                self._save()
                raise DomainError("MODEL_CALL_FAILED", "Agent调用失败，消费状态未知", 503)
            call = self.service._complete_call(call, response, self.job_id, self.worker_id)
            response = self.service._response(call)
        step = self._steps(key)
        step.update(status="succeeded", response=deepcopy(response), model_call=self.service._call_public(call), budget=self._budget())
        if response.get("finish_reason") != "stop":
            step["status"] = "failed"
            self._save()
            raise DomainError("AGENT_OUTPUT_INCOMPLETE", "Agent步骤响应未正常完成", 502)
        self._save()
        return response, call

    def _retrieve(self, key, decision):
        step = self._steps(key)
        if step is None:
            if self._supplemental_count() >= self.config["max_supplemental_calls"]:
                self.trace["stop_reason"] = "supplemental_budget_exhausted"
                self._save()
                raise DomainError("BUDGET_EXHAUSTED", "Agent补检索预算已耗尽", 409)
            step = {"key": key, "kind": "retrieval", "status": "reserved", "decision": deepcopy(decision),
                    "attempt_number": self.attempt,
                    "input_context_sha256": self.answer["context"]["context_sha256"]}
            self.trace["steps"].append(step)
            # The durable reservation counts even if retrieval is interrupted.
            self._save()
            step = self._steps(key)
        if step.get("status") == "succeeded":
            self.answer["context"] = deepcopy(step["context"])
            self.trace["candidate_pools"] = deepcopy(step["candidate_pools"])
            return
        if step.get("attempt_number") != self.attempt:
            step.update(status="uncertain", error_code="AGENT_RETRIEVAL_OUTCOME_UNKNOWN")
            self.trace["stop_reason"] = "retrieval_outcome_unknown"
            self._save()
            raise DomainError("AGENT_RETRIEVAL_OUTCOME_UNKNOWN", "补检索中断且无冻结结果，不自动重复消耗工具预算", 409)
        self.checkpoint("agent_retrieval", self.question["key"])
        retrieval = self.request["retrieval"]
        found = self.service.indexes.search(self.request["index_id"], decision["query"],
            self.request["stock_code"], self.request["report_year"],
            annual_top_k=retrieval["annual_top_k"], regulation_top_k=retrieval["regulation_top_k"],
            temporal_filter=retrieval["temporal_filter"], target_date=self.question["declared_target_date"],
            target_date_role=self.question["target_date_role"], unknown_temporal=retrieval["unknown_temporal"],
            heartbeat=lambda: self.checkpoint("agent_retrieval", self.question["key"]), deadline=self.deadline)
        roles = ("annual_report", "regulation") if decision["source_role"] == "both" else (decision["source_role"],)
        pools = deepcopy(self.trace["candidate_pools"])
        for role in roles:
            # Supplemental candidates are considered first. Whole chunks, roles,
            # duplicates and budgets are revalidated before final IDs are assigned.
            pools[role] = deepcopy(found[role]) + pools[role]
        context = assemble_context(pools, retrieval["max_reference_tokens"], self.counter, self.identity)
        context["budget_unit"] = self.identity["budget_unit"]
        self.answer["context"] = context
        self.trace["candidate_pools"] = pools
        self.trace["supplemental_calls_used"] = sum(s["kind"] == "retrieval" for s in self.trace["steps"])
        step = self._steps(key)
        step.update(status="succeeded", retrieval=found, selected_roles=list(roles), context=deepcopy(context),
                    context_sha256=context["context_sha256"], candidate_pools=deepcopy(pools), budget=self._budget())
        self._save()

    def execute(self):
        from app.evaluation.service import make_prompt
        for number in range(1, self.config["max_rounds"] + 1):
            key = "plan:" + str(number)
            history = [{k: step.get(k) for k in ("key", "kind", "status", "decision", "context_sha256")}
                       for step in self.trace["steps"] if step["kind"] in {"plan", "retrieval"}]
            prompt = make_plan_prompt(self.question, self.answer["context"], history, self._budget(), self.protocol)
            response, _ = self._model_step(key, "plan", prompt)
            decision = parse_plan(response["text"])
            self._steps(key)["decision"] = decision
            self._save()
            if decision["action"] == "stop":
                self.trace["stop_reason"] = "planner_stop"
                self._save()
                break
            self._retrieve("retrieve:" + str(number), decision)
        else:
            self.trace["stop_reason"] = "max_rounds_reached"
            self._save()
        if not self.answer["context"]["source_records"]:
            raise DomainError("BUDGET_EXHAUSTED", "Agent最终上下文没有完整证据片段", 409)
        # No retrieval occurs after this point: every draft/review/revision shares
        # this exact context and its local A/R identifiers.
        prompt = make_prompt(self.question, self.answer["context"], self.request["stock_code"],
                             self.request["report_year"], self.request["prompt_template"]["system"])
        response, call = self._model_step("draft", "draft", prompt)
        self.answer["prompt"] = deepcopy(self._steps("draft")["prompt"])
        self.service._validate_response(self.answer, call, response)
        self.trace["draft"] = {"answer_text": self.answer["answer_text"], "claims": deepcopy(self.answer["claims"]),
            "validation": deepcopy(self.answer["validation"]), "limitations": deepcopy(self.answer["limitations"])}
        self.answer["status"] = "running"
        self._save()
        review_prompt = make_verify_prompt(self.question, self.answer["context"], self.trace["draft"], self.protocol)
        reviewed, _ = self._model_step("verify", "verify", review_prompt)
        verification = parse_verification(reviewed["text"])
        self.trace["verification"] = verification | {"model_judgement_only": True,
            "semantic_support_verified": False, "human_review_status": "not_reviewed"}
        self._steps("verify")["decision"] = verification
        self._save()
        if verification["decision"] == "revise":
            if not self.config["allow_revision"]:
                raise DomainError("AGENT_REVISION_DISABLED", "模型要求修订，但冻结配置禁止修订", 409)
            revised_prompt = make_prompt(self.question, self.answer["context"], self.request["stock_code"],
                self.request["report_year"], self.request["prompt_template"]["system"])
            user = json.loads(revised_prompt["messages"][1]["content"])
            user["revision_request"] = {"draft": self.trace["draft"], "verification": verification,
                "instruction": "仅使用同一最终上下文修订，返回完整严格评价JSON；不得新增来源或沿用其他轮编号。"}
            revised_prompt["messages"][1]["content"] = dump(user)
            revised_prompt["messages_sha256"] = sha(dump(revised_prompt["messages"]).encode())
            revised, revised_call = self._model_step("revise", "revision", revised_prompt)
            self.answer["prompt"] = deepcopy(self._steps("revise")["prompt"])
            self.service._validate_response(self.answer, revised_call, revised)
            self.trace["revised"] = True
            self.trace["final_verification_status"] = "not_reverified_after_revision"
            self.answer["limitations"].append("Agent仅对修订前草稿作模型自检；最终修订通过机械校验后未另作模型复核。")
        else:
            self.service._validate_response(self.answer, call, response)
            self.trace["final_verification_status"] = "model_review_of_draft_only"
        self.answer["limitations"].append("Agent规划与自检不构成人工确认、法律适用审查或相较固定流程的质量提升证据。")
        self.trace["final_context_sha256"] = self.answer["context"]["context_sha256"]
        self.trace["final_budget"] = self._budget()
        self._save()
        return self.answer
