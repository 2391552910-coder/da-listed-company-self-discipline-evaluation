"""Protocol grouping is descriptive; it is not an Agent efficacy claim."""
from collections import defaultdict

from app.documents.common import dump, sha


def signature(request):
    fields = ("stock_code", "report_year", "index_manifest_sha256", "questions", "retrieval",
              "tokenizer_identity", "model", "budget", "prompt_template")
    return sha(dump({k: request.get(k) for k in fields}).encode())


def compare_runs(runs, ratings=None, historical=None):
    groups, summaries = {}, []
    for run in runs:
        request = run["request"]
        key = signature(request)
        mode = request.get("mode", "dual_fixed")
        group = groups.setdefault(key, {"signature": key, "run_ids": [], "modes": {},
            "comparable": False, "controlled_differences": ["mode", "agent_config", "agent_protocol"],
            "reasons": [], "stock_code": request.get("stock_code"), "report_year": request.get("report_year")})
        group["run_ids"].append(run["id"])
        group["modes"].setdefault(mode, []).append(run["id"])
        calls = run.get("model_calls", [])
        known = [c.get("usage", {}).get("total_tokens") for c in calls
                 if c.get("usage", {}).get("status") == "reported"]
        summaries.append({"run_id": run["id"], "mode": mode, "origin": run.get("origin", "new_run"),
            "status": run["status"], "model_call_count": len(calls),
            "observed_total_tokens": sum(v for v in known if type(v) is int),
            "provider_usage_complete": all(c.get("usage", {}).get("status") == "reported" for c in calls),
            "quality_score": None, "quality_status": "not_automatically_computed", "signature": key})
    for group in groups.values():
        group["comparable"] = {"dual_fixed", "dual_agent"} <= set(group["modes"])
        group["reasons"] = (["共同公司年度、索引清单、问题、检索、模型、提示词与预算一致；方法模式为受控差异。"]
            if group["comparable"] else ["该协议组缺少固定或Agent运行，不进行跨组比较。"])
    score_groups = defaultdict(list)
    for rating in ratings or []:
        score_groups[(rating["source_kind"], rating["rubric_version"])].append(rating)
    rating_groups = [{"source_kind": key[0], "rubric_version": key[1], "record_count": len(records),
                      "records": records, "quality_mean": None}
                     for key, records in sorted(score_groups.items())]
    return {"groups": list(groups.values()), "runs": summaries, "rating_groups": rating_groups,
        "historical_records": historical or [], "limitations": [
            "可比条件分组仅用于检查共同协议，不能证明样本代表性、语义支持或Agent质量收益。",
            "执行成功率、耗时和调用用量是运行指标，不作为回答质量分数。",
            "人工、自动评分及历史记录分别展示；未评分、未运行或技术失败不填0分。",
            "追加评分保留全部记录与更正链，不自动混合量表版本、评分来源或计算质量均值。"]}
