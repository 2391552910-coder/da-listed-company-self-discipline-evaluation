"""Replay stage2 real sources through queued CUDA retrieval and frozen Qwen budgets."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.documents.common import sha
from app.documents.indexes import Indexes
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once
from app.retrieval.context_builder import assemble_context
from app.retrieval.service import Contexts
from app.retrieval.token_budget import load_counter

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stage2-state",type=Path,required=True)
    p.add_argument("--work-dir",type=Path,required=True)
    p.add_argument("--model-python",required=True)
    p.add_argument("--stock-code",default="603288")
    p.add_argument("--year",type=int,default=2023)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    args.work_dir.mkdir(parents=True,exist_ok=False)
    shutil.copy2(args.stage2_state/"validation.sqlite",args.work_dir/"validation.sqlite")
    shutil.copytree(args.stage2_state/"assets",args.work_dir/"assets")
    settings=Settings("sqlite:///"+str((args.work_dir/"validation.sqlite").resolve()),
                      args.work_dir/"assets",args.model_python)
    docs=Documents(settings);docs.db.migrate()
    indexes=Indexes(docs).list()
    index=next(row for row in indexes if row["profile"]=="qwen_cuda" and row["status"]=="ready")
    service=Contexts(docs)
    queued=service.enqueue(index["id"],"审计委员会履职与信息披露义务有哪些依据",args.stock_code,args.year,
                           max_reference_tokens=6000,tokenizer_kind="qwen3",key="real-stage3-context")
    reopened=Documents(settings)
    assert reopened.job(queued["id"])["status"]=="queued"
    job=work_once(reopened)
    if job["status"]!="succeeded":raise RuntimeError(job)
    run=Contexts(reopened).get(queued["run_id"])
    snapshot=run["snapshot"];context=snapshot["context"]
    assert sha(context["context_text"].encode())==context["context_sha256"]
    assert context["reference_token_count"]<=6000
    checks=[]
    for record in context["source_records"]:
        source=reopened.source(record["chunk_id"])
        assert source["content"]==record["content"]
        assert source["frame_text"][source["char_start"]:source["char_end"]]==record["content"]
        checks.append({"evidence_id":record["evidence_id"],"role":record["source_role"],
                       "physical_page":record["physical_page"],"file_sha256":source["file_sha256"],
                       "content_sha256":record["content_sha256"],"locator_verified":True})
    assert checks
    replay=Contexts(reopened).get(queued["run_id"])
    assert replay["snapshot_sha256"]==run["snapshot_sha256"]
    counter,identity=load_counter("qwen3",settings,context["tokenizer_identity"])
    replays=[]
    for budget in [1,1000,2000,6000]:
        assembled=assemble_context({role:snapshot["retrieval"][role] for role in ("annual_report","regulation")},
                                   budget,counter,identity)
        assert assembled["reference_token_count"]<=budget
        replays.append({"budget":budget,"actual_reference_tokens":assembled["reference_token_count"],
                        "evidence_count":len(assembled["source_records"]),"omitted_count":len(assembled["omitted_records"]),
                        "context_sha256":assembled["context_sha256"],"diagnostics":assembled["diagnostics"]})
    result={"schema_version":"stage3-real-context-validation-v1","executed_at_utc":datetime.now(timezone.utc).isoformat(),
            "status":"passed","database_profile":"sqlite_diagnostic","retrieval_profile":"qwen_cuda",
            "generation_api_calls":0,"index_manifest_sha256":index["manifest_sha256"],
            "snapshot_sha256":run["snapshot_sha256"],"context_sha256":context["context_sha256"],
            "tokenizer_identity":identity,"max_reference_tokens":6000,"actual_reference_tokens":context["reference_token_count"],
            "candidate_counts":{role:len(snapshot["retrieval"][role]) for role in ("annual_report","regulation")},
            "included_count":len(checks),"omitted_count":len(context["omitted_records"]),"locator_checks":checks,
            "replayed_snapshot_unchanged":True,"cached_budget_replays":replays,
            "semantic_support_verified":False,"not_verified":["full server deployment","browser interaction"]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
