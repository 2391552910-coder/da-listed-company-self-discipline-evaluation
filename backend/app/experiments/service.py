"""Resumable batch dispatch and append-only, provenance-bound experiment records.

Dispatch only queues evaluation jobs. It never calls an external model itself.
An interrupted API submission resumes with deterministic per-cell idempotency
keys; cancellation is checked both before and after binding a newly queued run.
"""
from collections import Counter
import csv
import io
import json
import re

from app.documents.common import DomainError, code, dump, now, sha, uid
from app.documents.jobs import JobControl
from app.documents.service import required
from app.evaluation.questions import normalize_questions
from app.evaluation.service import Evaluations, SYSTEM_PROMPT, PROMPT_VERSION, RETRIEVAL_DEFAULTS, BUDGET_DEFAULTS
from app.experiments.comparison import compare_runs
from app.retrieval.temporal import select_regulations
from app.retrieval.token_budget import load_counter

CRITERIA = ("faithfulness", "coverage", "professionalism", "citation_quality")
RUBRIC_VERSION = "evidence-quality-v1"
TERMINAL = {"succeeded", "partial", "failed", "cancelled", "budget_exhausted"}


def _text(value, name, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise DomainError("INVALID_EXPERIMENT", name + "须为长度合法的非空文本", 422)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise DomainError("INVALID_EXPERIMENT", name + "包含无效Unicode字符", 422)
    return value.strip()


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise DomainError("INVALID_EXPERIMENT", name + "不合法", 422)
    return value


def _key(key):
    if key is not None:
        return _text(key, "幂等键", 200)
    return None


def _unique(error):
    state = getattr(error, "sqlstate", None) or getattr(getattr(error, "orig", None), "sqlstate", None)
    return state == "23505" or (error.__class__.__name__ == "IntegrityError" and "UNIQUE" in str(error).upper())


class Experiments:
    def __init__(self, documents, evaluations=None):
        self.docs = documents
        self.evaluations = evaluations or Evaluations(documents)

    @staticmethod
    def rubrics():
        return {"items": [{"version": RUBRIC_VERSION, "name": "证据评价质量量表",
            "criteria": [{"key": k, "name": n} for k, n in zip(CRITERIA,
                ("忠实性", "覆盖度", "专业性", "引用质量"))], "min": 1, "max": 5,
            "description": "外部人工或自动评审记录；不是执行成功率，不自动证明语义支持。"}]}

    def _public_request(self, name, cases, modes, question_set_version, questions, retrieval, budget,
                        agent, model, max_total_model_calls):
        name = _text(name, "实验名称")
        template = normalize_questions(question_set_version, questions)
        if not isinstance(cases, list) or not 1 <= len(cases) <= 8:
            raise DomainError("INVALID_EXPERIMENT", "实验须有1至8个公司年度case", 422)
        normalized, seen = [], set()
        for case in cases:
            if not isinstance(case, dict) or set(case) != {"key", "index_id", "stock_code", "report_year"}:
                raise DomainError("INVALID_EXPERIMENT", "case字段须为key/index_id/stock_code/report_year", 422)
            key = _text(case["key"], "case key", 64)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key) or key in seen:
                raise DomainError("INVALID_EXPERIMENT", "case key须唯一且仅含字母数字下划线短横线", 422)
            seen.add(key)
            normalized.append({"key": key, "index_id": _text(case["index_id"], "index_id", 64),
                "stock_code": code(case["stock_code"]),
                "report_year": _integer(case["report_year"], 1990, 2100, "report_year")})
        modes = ["dual_fixed", "dual_agent"] if modes is None else modes
        if (not isinstance(modes, list) or not modes or len(modes) > 2 or any(not isinstance(m, str) for m in modes) or len(set(modes)) != len(modes)
            or any(m not in {"dual_fixed", "dual_agent"} for m in modes)):
            raise DomainError("INVALID_EXPERIMENT", "模式须为不重复的dual_fixed/dual_agent", 422)
        if retrieval is not None and not isinstance(retrieval, dict):
            raise DomainError("INVALID_EXPERIMENT", "retrieval须为对象", 422)
        if budget is not None and not isinstance(budget, dict):
            raise DomainError("INVALID_EXPERIMENT", "budget须为对象", 422)
        if set(retrieval or {}) - set(RETRIEVAL_DEFAULTS) or set(budget or {}) - set(BUDGET_DEFAULTS):
            raise DomainError("INVALID_EXPERIMENT", "未知检索/预算字段", 422)
        retrieval = RETRIEVAL_DEFAULTS | (retrieval or {})
        budget = BUDGET_DEFAULTS | {"max_model_calls": 32} | (budget or {})
        for key, low, high in (("max_model_calls", 1, 40), ("max_observed_tokens", 1, 1000000),
                ("max_runtime_seconds", 1, 7200), ("max_output_tokens", 128, 16000),
                ("request_timeout_seconds", 1, 600)):
            _integer(budget[key], low, high, key)
        for key in ("annual_top_k", "regulation_top_k"):
            _integer(retrieval[key], 1, 20, key)
        _integer(retrieval["max_reference_tokens"], 1, 32000, "max_reference_tokens")
        if retrieval["tokenizer_kind"] not in {"qwen3", "utf8_diagnostic"}:
            raise DomainError("INVALID_EXPERIMENT", "未知参考计数器", 422)
        for question in template["questions"]:
            select_regulations([], temporal_filter=retrieval["temporal_filter"],
                target_date=question["declared_target_date"], target_date_role=question["target_date_role"],
                unknown_policy=retrieval["unknown_temporal"])
        agent_config = None
        required_calls = len(template["questions"])
        if "dual_agent" in modes:
            from app.evaluation.agent import normalize_agent_config
            agent_config = normalize_agent_config(agent)
            required_calls *= agent_config["max_rounds"] + 2 + int(agent_config["allow_revision"])
        elif agent is not None:
            raise DomainError("INVALID_EXPERIMENT", "固定模式实验不得附Agent配置", 422)
        if budget["max_model_calls"] < required_calls:
            raise DomainError("BUDGET_TOO_SMALL", "单运行预算不足以覆盖计划题目和Agent步骤", 422)
        cap = _integer(max_total_model_calls, 1, 128, "max_total_model_calls")
        declared = len(normalized) * len(modes) * budget["max_model_calls"]
        if declared > cap:
            raise DomainError("EXPERIMENT_BUDGET_TOO_SMALL", "所有子运行调用上限之和超过实验总预算", 422)
        if model is not None:
            model = _text(model, "model", 160)
        return {"name": name, "cases": normalized, "modes": modes, "question_set": template,
            "question_set_version": question_set_version, "retrieval": retrieval, "budget": budget,
            "agent": agent_config, "requested_model": model, "max_total_model_calls": cap,
            "declared_max_model_calls": declared}

    def _protocol(self, public):
        identity = self.evaluations.provider.identity(public["requested_model"])
        _, tokenizer = load_counter(public["retrieval"]["tokenizer_kind"], self.docs.settings)
        cases = []
        for case in public["cases"]:
            index, _, dataset, _ = self.evaluations.indexes.validate(case["index_id"])
            if not any(r["corpus_type"] == "annual_report" and r["stock_code"] == case["stock_code"]
                       and r["report_year"] == case["report_year"] for r in dataset["rows"]):
                raise DomainError("ANNUAL_SCOPE_EMPTY", "实验case指定公司年度无年报", 409)
            cases.append(case | {"index_manifest_sha256": index["manifest_sha256"]})
        protocol = public | {"version": "dual-source-experiment-v1", "cases": cases,
            "model": identity, "tokenizer_identity": tokenizer,
            "prompt_template": {"version": PROMPT_VERSION, "system": SYSTEM_PROMPT,
                "user_format": "stock-report-question-diagnostics-context-json-v1"},
            "origin": "new_run_protocol", "created_at": now()}
        if "dual_agent" in public["modes"]:
            from app.evaluation.agent_protocol import protocol_snapshot
            protocol["agent_protocol"] = protocol_snapshot()
        return json.loads(dump(protocol))

    def create(self, name, cases, modes=None, question_set_version="four-dim-v1", questions=None,
               retrieval=None, budget=None, agent=None, model=None, max_total_model_calls=128, key=None):
        key = _key(key)
        public = self._public_request(name, cases, modes, question_set_version, questions,
                                     retrieval, budget, agent, model, max_total_model_calls)
        digest = sha(dump(public).encode())
        if key:
            with self.docs.db.session() as db:
                old = db.one("SELECT * FROM t_experiment WHERE idempotency_key=?", (key,))
            if old:
                if old["request_sha256"] != digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应其他实验协议", 409)
                return self.dispatch(old["id"])
        protocol = self._protocol(public)
        experiment_id = uid()
        try:
            with self.docs.db.session() as db:
                db.execute("""INSERT INTO t_experiment
                    (id,name,request_json,request_sha256,protocol_json,protocol_sha256,idempotency_key,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (experiment_id, public["name"], dump(public), digest,
                    dump(protocol), sha(dump(protocol).encode()), key, now()))
                for case in protocol["cases"]:
                    for mode in protocol["modes"]:
                        child = {"index_id": case["index_id"], "stock_code": case["stock_code"],
                            "report_year": case["report_year"], "question_set_version": question_set_version,
                            "questions": protocol["question_set"]["questions"],
                            "retrieval": protocol["retrieval"], "budget": protocol["budget"],
                            "model": protocol["model"]["model"], "mode": mode}
                        if mode == "dual_agent":
                            child["agent"] = protocol["agent"]
                        db.execute("""INSERT INTO t_experiment_item
                            (id,experiment_id,case_key,mode,origin,status,request_json,created_at)
                            VALUES(?,?,?,?,?,?,?,?)""", (uid(), experiment_id, case["key"], mode,
                            "new_run", "pending", dump(child), now()))
        except Exception as error:
            if key and _unique(error):
                with self.docs.db.session() as db:
                    old = db.one("SELECT * FROM t_experiment WHERE idempotency_key=?", (key,))
                if old and old["request_sha256"] == digest:
                    return self.dispatch(old["id"])
                raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应其他实验协议", 409)
            raise
        return self.dispatch(experiment_id)

    def _experiment(self, experiment_id):
        with self.docs.db.session() as db:
            row = required(db.one("SELECT * FROM t_experiment WHERE id=?", (experiment_id,)))
        protocol = json.loads(row["protocol_json"])
        if sha(dump(protocol).encode()) != row["protocol_sha256"]:
            raise DomainError("EXPERIMENT_SNAPSHOT_MISMATCH", "实验协议快照哈希不一致", 409)
        return row, protocol

    def _assert_protocol_current(self, protocol, case):
        from app.evaluation import service as evaluation_module
        current, _, _, _ = self.evaluations.indexes.validate(case["index_id"])
        _, tokenizer = load_counter(protocol["retrieval"]["tokenizer_kind"], self.docs.settings)
        if (current["manifest_sha256"] != case["index_manifest_sha256"]
            or dump(self.evaluations.provider.identity(protocol["model"]["model"])) != dump(protocol["model"])
            or dump(tokenizer) != dump(protocol["tokenizer_identity"])
            or evaluation_module.SYSTEM_PROMPT != protocol["prompt_template"]["system"]
            or evaluation_module.PROMPT_VERSION != protocol["prompt_template"]["version"]):
            raise DomainError("EXPERIMENT_PROTOCOL_CHANGED", "模型、索引、计数器或提示词与冻结协议不一致", 409)
        if protocol.get("agent_protocol"):
            from app.evaluation.agent_protocol import protocol_snapshot
            if dump(protocol_snapshot()) != dump(protocol["agent_protocol"]):
                raise DomainError("EXPERIMENT_PROTOCOL_CHANGED", "Agent协议与冻结实验不一致", 409)

    @staticmethod
    def _assert_child(protocol, case, run, mode=None):
        request = run["request"]
        for key, expected in (("index_id", case["index_id"]), ("stock_code", case["stock_code"]),
            ("report_year", case["report_year"]), ("index_manifest_sha256", case["index_manifest_sha256"]),
            ("model", protocol["model"]), ("tokenizer_identity", protocol["tokenizer_identity"]),
            ("prompt_template", protocol["prompt_template"]),
            ("questions", protocol["question_set"]["questions"]), ("budget", protocol["budget"]),
            ("retrieval", protocol["retrieval"])):
            if dump(request.get(key)) != dump(expected):
                raise DomainError("EXPERIMENT_PROTOCOL_CHANGED", "子运行身份与实验冻结协议不一致", 409)
        if mode is not None and request.get("mode", "dual_fixed") != mode:
            raise DomainError("EXPERIMENT_PROTOCOL_CHANGED", "子运行模式与实验单元不一致", 409)
        if request.get("mode", "dual_fixed") == "dual_agent" and (
            dump(request.get("agent_config")) != dump(protocol["agent"])
            or dump(request.get("agent_protocol")) != dump(protocol["agent_protocol"])):
            raise DomainError("EXPERIMENT_PROTOCOL_CHANGED", "子运行Agent配置与实验不一致", 409)

    def dispatch(self, experiment_id):
        row, protocol = self._experiment(experiment_id)
        if row["cancel_requested"]:
            return self.cancel(experiment_id)
        with self.docs.db.session() as db:
            items = db.rows("SELECT * FROM t_experiment_item WHERE experiment_id=? AND run_id IS NULL AND origin='new_run' ORDER BY created_at,id", (experiment_id,))
        for item in items:
            with self.docs.db.session() as db:
                parent = required(db.one("SELECT cancel_requested FROM t_experiment WHERE id=?", (experiment_id,)))
            if parent["cancel_requested"]:
                return self.cancel(experiment_id)
            case = next(c for c in protocol["cases"] if c["key"] == item["case_key"])
            child_key = "experiment:" + experiment_id + ":" + item["id"]
            task = None
            created_here = verified_child = False
            try:
                with self.docs.db.session() as db:
                    existing = db.one("SELECT * FROM t_job WHERE idempotency_key=?", (child_key,))
                if existing:
                    if existing["kind"] != "evaluation":
                        raise DomainError("IDEMPOTENCY_CONFLICT", "实验子键对应其他任务类型", 409)
                    task = self.docs.public_job(existing)
                else:
                    self._assert_protocol_current(protocol, case)
                    task = self.evaluations.enqueue(**json.loads(item["request_json"]), key=child_key)
                    created_here = True
                run = self.evaluations.get(task["run_id"])
                self._assert_child(protocol, case, run, item["mode"])
                verified_child = True
                with self.docs.db.session() as db:
                    bound = db.execute("UPDATE t_experiment_item SET run_id=?,status='queued',error_code=NULL WHERE id=? AND run_id IS NULL",
                                       (task["run_id"], item["id"])).rowcount
                    actual = required(db.one("SELECT run_id FROM t_experiment_item WHERE id=?", (item["id"],)))
                    if not bound and actual["run_id"] != task["run_id"]:
                        raise DomainError("EXPERIMENT_CHILD_CONFLICT", "实验子项已经绑定另一运行", 409)
                    cancelled = db.one("SELECT cancel_requested FROM t_experiment WHERE id=?", (experiment_id,))["cancel_requested"]
                if cancelled:
                    JobControl(self.docs).cancel(task["id"])
            except DomainError as error:
                if task and (created_here or verified_child):
                    JobControl(self.docs).cancel(task["id"])
                with self.docs.db.session() as db:
                    db.execute("UPDATE t_experiment_item SET status='dispatch_failed',error_code=? WHERE id=? AND run_id IS NULL",
                               (error.code, item["id"]))
        return self.get(experiment_id)

    def cancel(self, experiment_id):
        row, protocol = self._experiment(experiment_id)
        if not row["cancel_requested"] and self.get(experiment_id)["status"] in TERMINAL:
            return self.get(experiment_id)
        with self.docs.db.session() as db:
            db.execute("UPDATE t_experiment SET cancel_requested=1 WHERE id=?", (experiment_id,))
            db.execute("UPDATE t_experiment_item SET status='cancelled',error_code='TASK_CANCELLED' WHERE experiment_id=? AND run_id IS NULL", (experiment_id,))
            items = db.rows("SELECT * FROM t_experiment_item WHERE experiment_id=? AND origin='new_run'", (experiment_id,))
        # Attaching an existing run does not grant this experiment ownership of it.
        # Reconcile both crash windows: enqueue before binding, and binding before
        # cancel. No external model call is made during this reconciliation.
        for item in items:
            with self.docs.db.session() as db:
                if item["run_id"]:
                    job = db.one("SELECT j.* FROM t_job j JOIN t_evaluation_run r ON r.job_id=j.id WHERE r.id=?", (item["run_id"],))
                else:
                    child_key = "experiment:" + experiment_id + ":" + item["id"]
                    job = db.one("SELECT * FROM t_job WHERE idempotency_key=?", (child_key,))
            if not job or job["kind"] != "evaluation":
                continue
            task = self.docs.public_job(job)
            case = next(c for c in protocol["cases"] if c["key"] == item["case_key"])
            run = self.evaluations.get(task["run_id"])
            try:
                self._assert_child(protocol, case, run, item["mode"])
            except DomainError as error:
                with self.docs.db.session() as db:
                    db.execute("UPDATE t_experiment_item SET status='dispatch_failed',error_code=? WHERE id=? AND run_id IS NULL", (error.code, item["id"]))
                continue
            with self.docs.db.session() as db:
                bound = db.execute("""UPDATE t_experiment_item SET run_id=?,status='cancelled',error_code='TASK_CANCELLED'
                    WHERE id=? AND (run_id IS NULL OR run_id=?)""", (run["id"], item["id"], run["id"])).rowcount
            if bound == 1:
                JobControl(self.docs).cancel(task["id"])
        return self.get(experiment_id)

    def attach(self, experiment_id, run_id, label=""):
        self._experiment(experiment_id)
        run = self.evaluations.get(run_id)
        mode = run["request"].get("mode", "dual_fixed")
        label = _text(label, "关联说明", 200) if label else "关联已有运行；不改变原运行协议"
        try:
            with self.docs.db.session() as db:
                db.execute("""INSERT INTO t_experiment_item
                    (id,experiment_id,case_key,mode,origin,status,run_id,label,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (uid(), experiment_id, "attached-" + run_id, mode,
                    "existing_run", run["status"], run_id, label, now()))
        except Exception as error:
            if not _unique(error):
                raise
        return self.get(experiment_id)

    @staticmethod
    def _rating(row):
        result = {k: row[k] for k in ("id", "experiment_id", "run_id", "answer_id", "answer_snapshot_sha256",
            "source_kind", "rubric_version", "reviewer", "method_version", "comment", "supersedes_score_id", "created_at")}
        result["values"] = json.loads(row["values_json"])
        return result

    def add_rating(self, experiment_id, run_id, answer_id, source_kind, rubric_version, values,
                   reviewer, method_version, comment="", supersedes_score_id=None, key=None):
        arguments = (experiment_id, run_id, answer_id, source_kind, rubric_version, values,
                     reviewer, method_version, comment, supersedes_score_id, key)
        try:
            return self._add_rating(*arguments)
        except Exception as error:
            if key and _unique(error):
                # A concurrent identical submission may win the UNIQUE index.
                # The losing insert rolled back; re-read and compare its digest.
                return self._add_rating(*arguments)
            raise

    def _add_rating(self, experiment_id, run_id, answer_id, source_kind, rubric_version, values,
                   reviewer, method_version, comment="", supersedes_score_id=None, key=None):
        self._experiment(experiment_id)
        key = _key(key)
        if source_kind not in {"manual", "automatic"} or rubric_version != RUBRIC_VERSION:
            raise DomainError("INVALID_RATING", "评分来源或量表版本不支持", 422)
        if (not isinstance(values, dict) or set(values) != set(CRITERIA)
            or any(v is not None and (type(v) is not int or not 1 <= v <= 5) for v in values.values())
            or all(v is None for v in values.values())):
            raise DomainError("INVALID_RATING", "评分须为1至5或null，至少一项有评分，失败不填0", 422)
        reviewer = _text(reviewer, "评审者/评审器", 200)
        method_version = _text(method_version, "评分方法版本", 200)
        if not isinstance(comment, str) or len(comment) > 4000:
            raise DomainError("INVALID_RATING", "评分说明过长", 422)
        request = {"experiment_id": experiment_id, "run_id": run_id, "answer_id": answer_id,
            "source_kind": source_kind, "rubric_version": rubric_version, "values": values,
            "reviewer": reviewer, "method_version": method_version, "comment": comment,
            "supersedes_score_id": supersedes_score_id}
        digest = sha(dump(request).encode())
        if key:
            with self.docs.db.session() as db:
                old = db.one("SELECT * FROM t_experiment_rating WHERE idempotency_key=?", (key,))
            if old:
                if old["request_sha256"] != digest:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应其他评分", 409)
                return self._rating(old)
        run = self.evaluations.get(run_id)
        answer = next((a for a in run["answers"] if a["id"] == answer_id), None)
        if not answer or answer["status"] != "succeeded" or not answer.get("answer_text"):
            raise DomainError("ANSWER_NOT_RATEABLE", "仅可评分实际完成的有效回答", 409)
        with self.docs.db.session() as db:
            if not db.one("SELECT id FROM t_experiment_item WHERE experiment_id=? AND run_id=?", (experiment_id, run_id)):
                raise DomainError("RUN_NOT_IN_EXPERIMENT", "该运行未关联当前实验", 409)
            source = required(db.one("SELECT snapshot_sha256 FROM t_evaluation_answer WHERE id=? AND run_id=?", (answer_id, run_id)))
            if supersedes_score_id:
                previous = required(db.one("SELECT * FROM t_experiment_rating WHERE id=?", (supersedes_score_id,)))
                if any(previous[k] != request[k] for k in ("experiment_id", "run_id", "answer_id", "source_kind", "rubric_version", "reviewer")):
                    raise DomainError("INVALID_RATING_CORRECTION", "更正须对应同实验、回答、来源、量表和评审者", 409)
            rating_id = uid()
            db.execute("""INSERT INTO t_experiment_rating
                (id,experiment_id,run_id,answer_id,answer_snapshot_sha256,source_kind,rubric_version,values_json,
                reviewer,method_version,comment,supersedes_score_id,request_sha256,idempotency_key,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (rating_id, experiment_id, run_id, answer_id,
                source["snapshot_sha256"], source_kind, rubric_version, dump(values), reviewer, method_version,
                comment, supersedes_score_id, digest, key, now()))
            return self._rating(required(db.one("SELECT * FROM t_experiment_rating WHERE id=?", (rating_id,))))

    def import_history(self, experiment_id, label, source_identity, records, key=None):
        try:
            return self._import_history(experiment_id, label, source_identity, records, key)
        except Exception as error:
            if key and _unique(error):
                return self._import_history(experiment_id, label, source_identity, records, key)
            raise

    def _import_history(self, experiment_id, label, source_identity, records, key=None):
        self._experiment(experiment_id)
        key = _key(key)
        label, source_identity = _text(label, "历史批次标签"), _text(source_identity, "历史来源身份", 500)
        if not isinstance(records, list) or not 1 <= len(records) <= 200:
            raise DomainError("INVALID_HISTORY", "历史记录须为1至200条", 422)
        normalized = []
        allowed = {"record_key", "mode", "status", "stock_code", "report_year", "question", "answer", "quality_scores", "notes"}
        for record in records:
            if not isinstance(record, dict) or set(record) - allowed or not {"record_key", "mode"} <= set(record):
                raise DomainError("INVALID_HISTORY", "历史记录含未支持字段或缺少record_key/mode", 422)
            item = dict(record)
            item["record_key"] = _text(item["record_key"], "历史记录key", 100)
            if item["mode"] not in {"dual_fixed", "dual_agent", "unknown"}:
                raise DomainError("INVALID_HISTORY", "历史mode不合法", 422)
            item["status"] = item.get("status", "unknown")
            if item["status"] not in {"succeeded", "failed", "not_run", "unknown"}:
                raise DomainError("INVALID_HISTORY", "历史status不合法", 422)
            if "stock_code" in item:
                item["stock_code"] = code(item["stock_code"])
            if "report_year" in item:
                _integer(item["report_year"], 1990, 2100, "report_year")
            for field in ("question", "answer", "notes"):
                if field in item and (not isinstance(item[field], str) or len(item[field]) > 20000):
                    raise DomainError("INVALID_HISTORY", "历史文本字段过长", 422)
            if "quality_scores" in item:
                values = item["quality_scores"]
                if not isinstance(values, dict) or set(values) - set(CRITERIA) or any(
                    v is not None and (type(v) is not int or not 1 <= v <= 5) for v in values.values()):
                    raise DomainError("INVALID_HISTORY", "历史评分须为1至5或null，不能填失败0分", 422)
                if item["status"] in {"failed", "not_run"} and any(v is not None for v in values.values()):
                    raise DomainError("INVALID_HISTORY", "失败或未运行的历史记录不能带质量分数", 422)
            normalized.append(item)
        if len({r["record_key"] for r in normalized}) != len(normalized):
            raise DomainError("INVALID_HISTORY", "同批历史记录key重复", 422)
        digest = sha(dump({"experiment_id": experiment_id, "label": label,
            "source_identity": source_identity, "records": normalized}).encode())
        with self.docs.db.session() as db:
            if key:
                old = db.one("SELECT * FROM t_experiment_history WHERE idempotency_key=?", (key,))
                if old:
                    if old["request_sha256"] != digest:
                        raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应其他历史导入", 409)
                    return self.get(experiment_id)
            db.execute("""INSERT INTO t_experiment_history
                (id,experiment_id,label,source_identity,records_json,request_sha256,idempotency_key,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (uid(), experiment_id, label, source_identity,
                dump(normalized), digest, key, now()))
        return self.get(experiment_id)

    def get(self, experiment_id):
        row, protocol = self._experiment(experiment_id)
        with self.docs.db.session() as db:
            items = db.rows("SELECT * FROM t_experiment_item WHERE experiment_id=? ORDER BY created_at,id", (experiment_id,))
            ratings = [self._rating(r) for r in db.rows("SELECT * FROM t_experiment_rating WHERE experiment_id=? ORDER BY created_at,id", (experiment_id,))]
            histories = db.rows("SELECT * FROM t_experiment_history WHERE experiment_id=? ORDER BY created_at,id", (experiment_id,))
        runs, public_items = [], []
        for item in items:
            result = {k: item[k] for k in ("id", "case_key", "mode", "origin", "status", "run_id", "error_code", "label")}
            result["job_id"] = None
            if item["run_id"]:
                run = self.evaluations.get(item["run_id"])
                run["origin"] = item["origin"]
                runs.append(run)
                result.update(status=run["status"], job_id=run["job_id"])
            public_items.append(result)
        counts = Counter(i["status"] for i in public_items)
        progress = {state: counts[state] for state in ("pending", "queued", "running", "succeeded", "partial",
            "failed", "cancelled", "budget_exhausted", "dispatch_failed")}
        progress["total"] = len(items)
        good = counts["succeeded"]
        active = sum(counts[s] for s in ("pending", "queued", "running"))
        owned_active = sum(i["origin"] == "new_run" and i["status"] in {"pending", "queued", "running"} for i in public_items)
        status = ("cancelling" if row["cancel_requested"] and owned_active else "cancelled" if row["cancel_requested"]
            else "running" if active else "succeeded" if good == len(items) and items
            else "partial" if good or counts["partial"] else "failed")
        historical = [{"id": h["id"], "origin": "historical_import", "label": h["label"],
            "source_identity": h["source_identity"], "records_sha256": sha(h["records_json"].encode()),
            "records": json.loads(h["records_json"]), "created_at": h["created_at"]} for h in histories]
        return {"id": row["id"], "name": row["name"], "status": status, "cancel_requested": bool(row["cancel_requested"]),
            "created_at": row["created_at"], "protocol": protocol, "protocol_sha256": row["protocol_sha256"],
            "items": public_items, "progress": progress, "ratings": ratings, "historical_records": historical,
            "comparison": compare_runs(runs, ratings, historical)}

    def list(self, page=1, size=20):
        _integer(page, 1, 1000000, "page")
        _integer(size, 1, 200, "page_size")
        with self.docs.db.session() as db:
            total = db.one("SELECT COUNT(*) AS n FROM t_experiment")["n"]
            rows = db.rows("SELECT id,name,protocol_sha256,cancel_requested,created_at FROM t_experiment ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (size, (page - 1) * size))
            statuses = {}
            for row in rows:
                cells = db.rows("""SELECT COALESCE(r.status,i.status) AS status,i.origin FROM t_experiment_item i
                    LEFT JOIN t_evaluation_run r ON r.id=i.run_id WHERE i.experiment_id=?""", (row["id"],))
                counts = Counter(cell["status"] for cell in cells)
                active = sum(counts[s] for s in ("pending", "queued", "running"))
                owned_active = sum(cell["origin"] == "new_run" and cell["status"] in {"pending", "queued", "running"} for cell in cells)
                statuses[row["id"]] = ("cancelling" if row["cancel_requested"] and owned_active
                    else "cancelled" if row["cancel_requested"] else "running" if active
                    else "succeeded" if cells and counts["succeeded"] == len(cells)
                    else "partial" if counts["succeeded"] or counts["partial"] else "failed")
        return {"items": [r | {"status": statuses[r["id"]]} for r in rows],
            "total": total, "page": page, "page_size": size}

    def export(self, experiment_id, format="json"):
        if format not in {"json", "csv"}:
            raise DomainError("INVALID_EXPERIMENT_EXPORT", "实验仅支持json/csv导出", 422)
        snapshot = self.get(experiment_id)
        data = dump(snapshot).encode()
        snapshot_hash = sha(data)
        snapshot_key = "experiment-exports/" + experiment_id + "/" + snapshot_hash + ".json"
        self.docs.storage.write(snapshot_key, data)
        if format == "json":
            content = data
        else:
            out = io.StringIO(newline="")
            writer = csv.writer(out)
            writer.writerow(["record_kind", "run_or_batch_id", "mode_or_source", "status_or_rubric", "question_or_answer_id", "criterion", "value", "provenance"])
            def safe(value):
                text = "" if value is None else str(value)
                return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
            for run in snapshot["comparison"]["runs"]:
                writer.writerow(["run", run["run_id"], run["mode"], run["status"], "", "quality_score", "", run["origin"]])
                writer.writerow(["operational", run["run_id"], run["mode"], run["status"], "", "observed_total_tokens", run["observed_total_tokens"], "reported usage only; not quality"])
                writer.writerow(["operational", run["run_id"], run["mode"], run["status"], "", "provider_usage_complete", run["provider_usage_complete"], "false means observed tokens are only a reported subset"])
            for rating in snapshot["ratings"]:
                for criterion, value in rating["values"].items():
                    writer.writerow(["rating", rating["run_id"], rating["source_kind"], rating["rubric_version"], rating["answer_id"], criterion,
                        "" if value is None else value, safe(rating["reviewer"] + ":" + rating["method_version"])])
            for history in snapshot["historical_records"]:
                for record in history["records"]:
                    writer.writerow(["historical_import", history["id"], record["mode"], "historical", safe(record["record_key"]), "quality_score", "", safe(history["label"] + ":" + history["source_identity"])])
            content = ("\ufeff" + out.getvalue()).encode("utf-8")
        file_hash = sha(content)
        file_key = "experiment-exports/" + experiment_id + "/" + file_hash + "." + format
        self.docs.storage.write(file_key, content)
        with self.docs.db.session() as db:
            db.execute("""INSERT INTO t_experiment_export
                (id,experiment_id,format,snapshot_key,snapshot_sha256,file_key,file_sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (uid(), experiment_id, format, snapshot_key, snapshot_hash, file_key, file_hash, now()))
        return content, {"media_type": "application/json" if format == "json" else "text/csv; charset=utf-8",
            "filename": "experiment-" + experiment_id + "-" + snapshot_hash[:8] + "." + format,
            "snapshot_sha256": snapshot_hash, "file_sha256": file_hash}
