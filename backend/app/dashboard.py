"""Read-only research overview with explicit scope and evidence-count semantics.

Operational counts come from business metadata. Evidence coverage is counted
only from verified, terminal evaluation snapshots, never from model quality
scores or live retrieval. This endpoint performs no model or source re-check.
"""
import json

from app.documents.common import DomainError, dump, now, sha
from app.documents.service import required
from app.evaluation.questions import DIMENSIONS
from app.evaluation.service import Evaluations


STATUSES = ("queued", "running", "succeeded", "partial", "failed", "cancelled", "budget_exhausted")
COVERAGE_RUN_LIMIT = 500


def _integer(value, low, high, name):
    if value is not None and (type(value) is not int or not low <= value <= high):
        raise DomainError("INVALID_DASHBOARD_SCOPE", name + "不合法", 422)


def _object(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


class Dashboard:
    def __init__(self, documents, evaluations=None):
        self.docs = documents
        self.evaluations = evaluations or Evaluations(documents)

    def get(self, company_id=None, year=None):
        _integer(company_id, 1, 2147483647, "company_id")
        _integer(year, 1990, 2100, "year")
        scoped = company_id is not None or year is not None
        with self.docs.db.session() as db:
            company = (required(db.one("SELECT id,stock_code,stock_name FROM t_company WHERE id=?", (company_id,)))
                       if company_id is not None else None)
            companies = 1 if company else db.one("SELECT COUNT(*) AS n FROM t_company")["n"]
            annual_where, annual_args = ["corpus_type='annual_report'"], []
            if company_id is not None:
                annual_where.append("company_id=?")
                annual_args.append(company_id)
            if year is not None:
                annual_where.append("report_year=?")
                annual_args.append(year)
            annual = db.rows("SELECT id FROM t_document WHERE " + " AND ".join(annual_where), annual_args)
            regulations = db.rows("SELECT id FROM t_document WHERE corpus_type='regulation'")
            annual_ids = {row["id"] for row in annual}
            regulation_ids = {row["id"] for row in regulations}
            allowed_documents = annual_ids | regulation_ids
            versions = db.rows("SELECT id,document_id FROM t_document_version")
            allowed_versions = {row["id"] for row in versions if row["document_id"] in allowed_documents}
            annual_versions = {row["id"] for row in versions if row["document_id"] in annual_ids}
            parses = db.rows("SELECT id,version_id FROM t_parse_run")
            annual_parses = {row["id"] for row in parses if row["version_id"] in annual_versions}
            indexes = db.rows("SELECT id,status,config_json FROM t_index_version")
            # Membership, not title strings, associates a ready index with scope.
            members = db.rows("""SELECT DISTINCT m.index_id,p.id AS parse_id
                FROM t_index_member m JOIN t_document_chunk c ON c.id=m.chunk_id
                JOIN t_parse_run p ON p.id=c.parse_id""")
            scoped_indexes = {row["index_id"] for row in members if row["parse_id"] in annual_parses}
            # Staging/failed jobs may not yet have members; frozen parse IDs bind
            # those jobs without widening to every index that shares a law.
            index_jobs = set(scoped_indexes)
            for row in indexes:
                parse_ids = _object(row["config_json"]).get("parse_ids", [])
                if isinstance(parse_ids, list) and any(isinstance(pid, str) and pid in annual_parses for pid in parse_ids):
                    index_jobs.add(row["id"])
            ready_indexes = sum(row["status"] == "ready" and (not scoped or row["id"] in scoped_indexes) for row in indexes)
            run_where, run_args = [], []
            if company:
                run_where.append("stock_code=?")
                run_args.append(company["stock_code"])
            if year is not None:
                run_where.append("report_year=?")
                run_args.append(year)
            condition = " WHERE " + " AND ".join(run_where) if run_where else ""
            total_runs = db.one("SELECT COUNT(*) AS n FROM t_evaluation_run" + condition, run_args)["n"]
            runs = db.rows("""SELECT id,job_id,index_id,stock_code,report_year,status,
                parent_run_id,created_at,request_json,request_sha256 FROM t_evaluation_run""" + condition +
                " ORDER BY created_at DESC,id DESC LIMIT ?", (*run_args, COVERAGE_RUN_LIMIT))
            # Task totals include all matching runs, not just the coverage window.
            evaluation_jobs = {row["job_id"] for row in db.rows("SELECT job_id FROM t_evaluation_run" + condition, run_args)}
            context_jobs = set()
            if scoped:
                for row in db.rows("SELECT job_id,request_json FROM t_retrieval_run"):
                    request = _object(row["request_json"])
                    if self._request_matches(request, company, year):
                        context_jobs.add(row["job_id"])
            jobs = db.rows("SELECT id,kind,status,payload_json FROM t_job")
            job_counts = {status: 0 for status in STATUSES}
            for row in jobs:
                include = not scoped
                if scoped:
                    payload = _object(row["payload_json"])
                    if row["kind"] == "parse":
                        include = isinstance(payload.get("version_id"), str) and payload["version_id"] in allowed_versions
                    elif row["kind"] == "index":
                        include = isinstance(payload.get("index_id"), str) and payload["index_id"] in index_jobs
                    elif row["kind"] == "evaluation":
                        include = row["id"] in evaluation_jobs
                    elif row["kind"] == "context":
                        include = row["id"] in context_jobs
                if include and row["status"] in job_counts:
                    job_counts[row["status"]] += 1
            # Select only the bounded coverage window's answer metadata. A join
            # avoids large SQL placeholder lists and works on both DB profiles.
            answers = db.rows("""SELECT a.id,a.run_id,a.dimension,a.status FROM t_evaluation_answer a
                JOIN (SELECT id FROM t_evaluation_run""" + condition +
                " ORDER BY created_at DESC,id DESC LIMIT ?) r ON r.id=a.run_id", (*run_args, COVERAGE_RUN_LIMIT))

        recent = []
        for row in runs[:5]:
            item = {key: row[key] for key in ("id", "job_id", "index_id", "stock_code", "report_year", "status", "parent_run_id", "created_at")}
            try:
                request = json.loads(row["request_json"])
                if not isinstance(request, dict) or sha(dump(request).encode()) != row["request_sha256"]:
                    raise ValueError("request identity mismatch")
                mode = request.get("mode", "dual_fixed")
            except (ValueError, TypeError, UnicodeError):
                mode = "unknown"
            item["mode"] = mode if mode in {"dual_fixed", "dual_agent"} else "unknown"
            recent.append(item)
        coverage = self._coverage(runs, answers, total_runs)
        return {"schema_version": "research-dashboard-v1", "generated_at": now(),
            "scope": {"company_id": company_id, "stock_code": company["stock_code"] if company else None,
                "company_name": company["stock_name"] if company else None, "year": year, "regulations": "shared_library"},
            "counts": {"companies": companies, "annual_documents": len(annual_ids),
                "regulation_documents": len(regulation_ids), "ready_indexes": ready_indexes, "evaluation_runs": total_runs},
            "job_scope": "selected_annual_and_shared_regulation",
            "job_statuses": [{"status": status, "count": job_counts[status]} for status in STATUSES],
            "recent_runs": recent, "coverage": coverage,
            "third_chapter_index": None,
            "notes": ["公司数为已登记身份数，仅受公司筛选影响；年度用于年报、索引、任务和评价。",
                "法规是共享资料库，不受公司或报告年度筛选；不表示该法规已适用于具体事项。",
                "就绪索引数来自业务状态及片段成员关系，不代表当前模型或文件仍可用。",
                "来源覆盖按运行中实际入选的冻结片段计数，重复及重试运行分别计次，不表示语义支持或评价质量。",
                "统计为读取时的持久化状态；活动任务可能在读取期间变化。"]}

    @staticmethod
    def _request_matches(request, company, year):
        return (isinstance(request.get("stock_code"), str)
                and type(request.get("report_year")) is int
                and (company is None or request["stock_code"] == company["stock_code"])
                and (year is None or request["report_year"] == year))

    def _coverage(self, runs, answers, total_runs):
        dimensions = {item["code"]: {**item, **{key: 0 for key in (
            "total_answers", "succeeded_answers", "failed_answers", "pending_answers", "unknown_answers",
            "annual_context_answers", "regulation_context_answers", "both_context_answers",
            "evidence_insufficient_answers", "mechanically_valid_citation_answers")}} for item in DIMENSIONS}
        by_run = {}
        for row in answers:
            by_run.setdefault(row["run_id"], []).append(row)
        issues = []
        for row in runs:
            raw_answers = by_run.get(row["id"], [])
            for answer in raw_answers:
                if answer["dimension"] in dimensions:
                    dimensions[answer["dimension"]]["total_answers"] += 1
            if row["status"] in {"queued", "running"}:
                for answer in raw_answers:
                    if answer["dimension"] in dimensions:
                        dimensions[answer["dimension"]]["pending_answers"] += 1
                continue
            try:
                run = self.evaluations.get(row["id"])
                if (run["request"].get("stock_code") != row["stock_code"]
                        or run["request"].get("report_year") != row["report_year"]):
                    raise DomainError("EVALUATION_SCOPE_MISMATCH", "运行与冻结请求范围不一致", 409)
                if run["status"] in {"queued", "running"}:
                    raise DomainError("EVALUATION_STATE_CHANGED", "运行状态在统计期间改变", 409)
                # Read source counts from the committed run snapshot. Cancelled
                # or crashed runs can retain individually saved answers without
                # publishing a final run, and those remain explicitly unknown.
                has_snapshot = run.get("snapshot") is not None
                published = {answer["id"]: answer for answer in
                             (run["snapshot"]["answers"] if has_snapshot else run["answers"])}
                if set(published) != {answer["id"] for answer in raw_answers}:
                    raise DomainError("EVALUATION_STATE_CHANGED", "运行答案在统计期间改变", 409)
                if has_snapshot:
                    independent = {answer["id"]: answer for answer in run["answers"]}
                    for answer in published.values():
                        if any(answer.get(key) != independent[answer["id"]].get(key) for key in answer):
                            raise DomainError("EVALUATION_SNAPSHOT_MISMATCH", "终态与单题快照不一致", 409)
                        if answer.get("status") != "succeeded":
                            continue
                        context = answer.get("context")
                        if not isinstance(context, dict) or not isinstance(context.get("source_records"), list):
                            raise DomainError("EVALUATION_SNAPSHOT_MISMATCH", "终态回答缺少冻结上下文", 409)
                        if any(not isinstance(record, dict) or record.get("source_role") not in {"annual_report", "regulation"}
                               for record in context["source_records"]):
                            raise DomainError("EVALUATION_SNAPSHOT_MISMATCH", "终态来源记录不合法", 409)
                        citations = (answer.get("validation") or {}).get("mechanical_citation_valid_count", 0)
                        if type(citations) is not int or citations < 0:
                            raise DomainError("EVALUATION_SNAPSHOT_MISMATCH", "终态引用计数不合法", 409)
            except (DomainError, ValueError, TypeError, KeyError, AttributeError, OSError) as error:
                issues.append({"run_id": row["id"], "error_code": error.code if isinstance(error, DomainError) else "EVALUATION_SNAPSHOT_UNREADABLE"})
                for answer in raw_answers:
                    if answer["dimension"] in dimensions:
                        dimensions[answer["dimension"]]["unknown_answers"] += 1
                continue
            if not has_snapshot and any(answer["status"] == "succeeded" for answer in published.values()):
                issues.append({"run_id": row["id"], "error_code": "EVALUATION_UNPUBLISHED_ANSWERS"})
            for raw in raw_answers:
                if raw["dimension"] not in dimensions:
                    continue
                item, answer = dimensions[raw["dimension"]], published[raw["id"]]
                if answer["status"] in {"failed", "cancelled", "budget_exhausted"}:
                    item["failed_answers"] += 1
                    continue
                if answer["status"] != "succeeded" or not has_snapshot:
                    item["unknown_answers"] += 1
                    continue
                item["succeeded_answers"] += 1
                records = (answer.get("context") or {}).get("source_records", [])
                roles = {record.get("source_role") for record in records}
                annual, regulation = "annual_report" in roles, "regulation" in roles
                item["annual_context_answers"] += annual
                item["regulation_context_answers"] += regulation
                item["both_context_answers"] += annual and regulation
                item["evidence_insufficient_answers"] += answer.get("evidence_status") == "insufficient"
                item["mechanically_valid_citation_answers"] += (answer.get("validation") or {}).get("mechanical_citation_valid_count", 0) > 0
        return {"basis": "frozen_answer_context_presence", "run_limit": COVERAGE_RUN_LIMIT,
            "total_runs": total_runs, "inspected_runs": len(runs), "omitted_runs": max(0, total_runs - len(runs)),
            "truncated": total_runs > len(runs), "dimensions": list(dimensions.values()), "issues": issues,
            "semantic_support_verified": False, "quality_score": None}
