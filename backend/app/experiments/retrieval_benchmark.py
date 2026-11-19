"""Persistent same-question retrieval measurements against a frozen judgment set."""
import csv
import io
import re
import time

from app.documents.common import DomainError, code, dump, sha
from app.documents.indexes import Indexes
from app.retrieval.methods import METHODS, RRF_K, validate_method


KIND = "retrieval_bench"
METRICS = ("precision", "recall", "hit_rate", "mrr", "judged_accuracy")


def metrics(ranking, relevant, irrelevant, k):
    ordered = ranking[:k]
    retrieved = set(ordered)
    hits = len(relevant & retrieved)
    first = next((i for i, key in enumerate(ordered, 1) if key in relevant), None)
    return {"precision": hits / k, "recall": hits / len(relevant), "hit_rate": int(bool(hits)),
            "mrr": 1 / first if first else 0,
            "judged_accuracy": (hits + len(irrelevant - retrieved)) / (len(relevant) + len(irrelevant))
                if irrelevant else None,
            "matched_relevant": hits, "known_relevant": len(relevant),
            "unjudged_returned": len(retrieved - relevant - irrelevant)}


class RetrievalBenchmark:
    def __init__(self, documents):
        self.documents = documents
        self.indexes = Indexes(documents)

    def enqueue(self, payload, key=None):
        methods, cases = payload["methods"], payload["cases"]
        candidate_k, top_k = payload["candidate_k"], payload["top_k"]
        if (not payload["name"].strip() or len(set(methods)) != len(methods) or candidate_k < top_k
                or len(methods) * len(cases) > 100):
            raise DomainError("INVALID_BENCHMARK", "名称、检索方法、候选数或实验规模不合法（最多100个对照单元）", 422)
        index, manifest, dataset, _ = self.indexes.validate(payload["index_id"])
        for method in methods:
            validate_method(index["profile"], method)
        rows = {row["id"]: row for row in dataset["rows"] if row["corpus_type"] == "annual_report"}
        used = set()
        for case in cases:
            key_name = case["key"]
            if key_name in used or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_name) or not case["query"].strip():
                raise DomainError("INVALID_BENCHMARK_CASE", "题号须唯一，查询不得为空", 422)
            used.add(key_name)
            case["stock_code"] = code(case["stock_code"])
            positives, negatives = case["relevant_chunk_ids"], case["irrelevant_chunk_ids"]
            keys = set(positives + negatives)
            if len(keys) != len(positives) + len(negatives):
                raise DomainError("DUPLICATE_JUDGMENT", f"{key_name} 含重复或冲突的相关性标注", 422)
            if any(key not in rows or rows[key]["stock_code"] != case["stock_code"]
                   or rows[key]["report_year"] != case["report_year"] for key in keys):
                raise DomainError("INVALID_JUDGMENT_SCOPE", f"{key_name} 的标注片段须来自该索引的指定公司年度", 422)
        frozen = payload | {"schema": "annual-retrieval-benchmark-v1", "rrf_k": RRF_K,
                            "index_manifest_sha256": index["manifest_sha256"], "profile": index["profile"],
                            "encoder_identity": manifest.get("encoder_identity")}
        with self.documents.db.session() as db:
            return self.documents._enqueue(db, KIND, frozen, key)

    def run(self, protocol, heartbeat, progress=None):
        index, _, _, _ = self.indexes.validate(protocol["index_id"])
        if index["manifest_sha256"] != protocol["index_manifest_sha256"]:
            raise DomainError("INDEX_SNAPSHOT_MISMATCH", "索引清单与冻结协议不符", 409)
        elapsed = time.monotonic()
        deadline = elapsed + protocol["max_runtime_seconds"]
        rows = []
        planned_count = len(protocol["methods"]) * len(protocol["cases"])
        for method in protocol["methods"]:
            for case in protocol["cases"]:
                heartbeat()
                if progress:
                    progress(len(rows), planned_count)
                row = {"case_key": case["key"], "dimension": case["dimension"], "method": method}
                try:
                    found = self.indexes.search(protocol["index_id"], case["query"], case["stock_code"],
                        case["report_year"], top_k=protocol["top_k"], method=method,
                        candidate_k=protocol["candidate_k"], heartbeat=heartbeat, deadline=deadline)
                    if found["manifest_sha256"] != protocol["index_manifest_sha256"]:
                        raise DomainError("INDEX_SNAPSHOT_MISMATCH", "索引清单与冻结协议不符", 409)
                except DomainError as error:
                    if error.code in {"TASK_CANCELLED", "LEASE_LOST", "INDEX_SNAPSHOT_MISMATCH"}:
                        raise
                    rows.append(row | {"status": "failed", "error_code": error.code})
                    continue
                ranking = [h["chunk_id"] for h in found["annual_report"]]
                rows.append(row | {"status": "succeeded", "ranking": ranking,
                    "elapsed_seconds": found["elapsed_seconds"],
                    **metrics(ranking, set(case["relevant_chunk_ids"]),
                              set(case["irrelevant_chunk_ids"]), protocol["top_k"])})
        summary = []
        for method in protocol["methods"]:
            for dimension in ("all", *sorted({c["dimension"] for c in protocol["cases"]})):
                planned = [r for r in rows if r["method"] == method and (dimension == "all" or r["dimension"] == dimension)]
                scored = [r for r in planned if r["status"] == "succeeded"]
                values = {}
                for name in METRICS:
                    observed = [r[name] for r in scored if r[name] is not None]
                    values[name] = sum(observed) / len(observed) if observed else None
                summary.append({"method": method, "dimension": dimension, "planned": len(planned),
                                "scored": len(scored), "accuracy_cases": sum(r["judged_accuracy"] is not None for r in scored),
                                **values})
        succeeded = sum(r["status"] == "succeeded" for r in rows)
        status = "succeeded" if succeeded == len(rows) else "partial" if succeeded else "failed"
        return {"status": status, "error_code": rows[0].get("error_code") if not succeeded else None,
                "protocol": protocol, "protocol_sha256": sha(dump(protocol).encode()), "summary": summary, "rows": rows}

    def get(self, job_id):
        job = self.documents.job(job_id)
        if job["kind"] != KIND:
            raise DomainError("NOT_FOUND", "检索对照任务不存在", 404)
        return job


def report(result, format):
    if format == "json":
        return dump(result), "application/json"
    if format == "csv":
        out = io.StringIO(newline="")
        fields = ["method", "dimension", "planned", "scored", "accuracy_cases", *METRICS]
        writer = csv.DictWriter(out, fields)
        writer.writeheader()
        writer.writerows(result["summary"])
        return "\ufeff" + out.getvalue(), "text/csv"
    protocol = result["protocol"]
    lines = ["# 年报检索对照报告", "", f'索引 ID `{protocol["index_id"]}`',
             f'冻结协议 SHA256 `{result["protocol_sha256"]}`',
             f'Top K = {protocol["top_k"]}，候选上限 = {protocol["candidate_k"]}，RRF k = {protocol["rrf_k"]}。', "",
             "| 方法 | 维度 | 已评测 / 计划 | 精确率 | 召回率 | 命中率 | MRR | 已标注准确率 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in result["summary"]:
        values = [f"{row[key]:.2%}" if row[key] is not None else "—" for key in METRICS]
        lines.append("| " + " | ".join([METHODS[row["method"]], row["dimension"],
                                        f'{row["scored"]}/{row["planned"]}', *values]) + " |")
    lines += ["", "按成功题目宏平均。精确率以 K 作分母，仅统计已标注的正例。准确率仅在同时提供正负标注时计算。",
              "失败单元不补零。未标注片段可能相关，结果应结合标注覆盖理解。", "", "## 逐题结果", "",
              "| 方法 | 题号 | 状态 | 命中 / 正例 | 耗时（秒） | 错误 |", "| --- | --- | --- | ---: | ---: | --- |"]
    for row in result["rows"]:
        lines.append("| " + " | ".join([METHODS[row["method"]], row["case_key"], row["status"],
            f'{row["matched_relevant"]}/{row["known_relevant"]}' if row["status"] == "succeeded" else "—",
            str(row.get("elapsed_seconds", "—")), row.get("error_code", "—")]) + " |")
    return "\n".join(lines) + "\n", "text/markdown"
