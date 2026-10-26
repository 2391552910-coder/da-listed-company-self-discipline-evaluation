"""Explicit real-model acceptance; never substitutes a fixture or cached answer.

Copies an existing real-source validation state, runs a single question first,
then the versioned four-dimensional set, and exports both frozen reports. The
summary contains hashes/counts only; raw sources, prompts, responses and secrets
stay in the selected local state directory.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.documents.common import sha
from app.documents.indexes import Indexes
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once
from app.evaluation.service import Evaluations
from app.reports.service import Reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-state", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model-python", required=True)
    parser.add_argument("--llm-env-file", type=Path, required=True)
    parser.add_argument("--stock-code", default="603288")
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from dotenv import dotenv_values
    values = dotenv_values(args.llm_env_file, encoding="utf-8-sig")
    args.work_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.baseline_state / "validation.sqlite", args.work_dir / "validation.sqlite")
    shutil.copytree(args.baseline_state / "assets", args.work_dir / "assets")
    settings = replace(Settings.from_env(),
        database_url="sqlite:///" + str((args.work_dir / "validation.sqlite").resolve()),
        data_root=(args.work_dir / "assets").resolve(), model_python=args.model_python,
        llm_base_url=values.get("LLM_BASE_URL", ""), llm_api_key=values.get("LLM_API_KEY", ""),
        llm_model=values.get("LLM_MODEL", ""))
    docs = Documents(settings)
    docs.db.migrate()
    index = next(i for i in Indexes(docs).list() if i["profile"] == "qwen_cuda" and i["status"] == "ready")
    result = {"schema_version": "stage45-real-validation-v1", "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "database_profile": "sqlite_diagnostic", "retrieval_profile": "qwen_cuda",
        "index_manifest_sha256": index["manifest_sha256"], "runs": [], "real_model_answers_received": 0,
        "semantic_support_verified": False, "third_chapter_index": None}
    for name, questions in (("single", [{"key": "D2-SINGLE", "dimension": "D2",
            "text": "审计委员会履职与信息披露义务有哪些可观察依据及资料限制？"}]), ("four_dimensions", None)):
        evaluations = Evaluations(docs)
        job = evaluations.enqueue(index["id"], args.stock_code, args.year, questions=questions,
            retrieval={"tokenizer_kind": "qwen3", "max_reference_tokens": 6000},
            budget={"max_model_calls": 4, "max_output_tokens": 8000, "max_observed_tokens": 80000,
                    "request_timeout_seconds": 600, "max_runtime_seconds": 3600}, key="real-stage45-" + name)
        assert docs.job(job["id"])["status"] == "queued"
        terminal = work_once(docs)
        run = Evaluations(Documents(settings)).get(job["run_id"])
        assert terminal["id"] == job["id"]
        checks = []
        for answer in run["answers"]:
            records = (answer.get("context") or {}).get("source_records", [])
            for record in records:
                source = docs.source(record["chunk_id"])
                assert source["source_available"]
                assert source["frame_text"][record["char_start"]:record["char_end"]] == record["content"]
                assert sha(record["content"].encode()) == record["content_sha256"]
            checks.append({"question_key": answer["question_key"], "status": answer["status"],
                "error_code": answer["error_code"], "locator_verified_count": len(records),
                "reference_token_count": (answer.get("context") or {}).get("reference_token_count"),
                "validation_status": (answer.get("validation") or {}).get("status"),
                "evidence_status": answer.get("evidence_status")})
        successful_calls = sum(c["status"] == "succeeded" for c in run["model_calls"])
        result["real_model_answers_received"] += successful_calls
        item = {"mode": name, "run_id": run["id"], "status": run["status"],
            "snapshot_sha256": run["snapshot_sha256"], "checks": checks,
            "model_calls": run["model_calls"], "exports": []}
        if run["status"] in {"succeeded", "partial"}:
            for format in ("html", "pdf"):
                export = Reports(docs).create(run["id"], format=format)
                data, metadata = Reports(Documents(settings)).download(export["id"])
                assert sha(data) == metadata["file_sha256"]
                item["exports"].append({k: export[k] for k in ("id", "format", "file_sha256", "snapshot_sha256", "size_bytes")})
        result["runs"].append(item)
        if run["status"] != "succeeded":
            result["status"] = "blocked" if not successful_calls else "failed"
            result["blocking_errors"] = sorted({a.get("error_code") for a in run["answers"] if a.get("error_code")})
            result["unverified"] = ["successful real-model single question", "successful real-model four dimensions", "exports of real-model evaluations"]
            break
    else:
        result["status"] = "passed"
        result["unverified"] = ["independent human semantic review", "full server deployment"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
