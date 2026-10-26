"""Run real-source acceptance without generation API calls.

Uses the same services/Worker as HTTP. Inputs stay local; output contains hashes,
counts and validation results, never absolute paths or raw source documents.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.documents.common import sha
from app.documents.indexes import Indexes
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--annual",type=Path,required=True)
    p.add_argument("--regulation",type=Path,required=True)
    p.add_argument("--stock-code",required=True)
    p.add_argument("--stock-name",required=True)
    p.add_argument("--year",type=int,required=True)
    p.add_argument("--model-python",required=True)
    p.add_argument("--reference-root",type=Path)
    p.add_argument("--work-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--profile",choices=["qwen_cuda","bm25_diagnostic"],default="qwen_cuda")
    args=p.parse_args()
    args.work_dir.mkdir(parents=True,exist_ok=False)
    docs=Documents(Settings("sqlite:///"+str((args.work_dir/"validation.sqlite").resolve()),
                            args.work_dir/"assets",args.model_python))
    docs.db.migrate()
    company=docs.company(args.stock_code,args.stock_name)
    sources=[]
    for role,path in (("annual_report",args.annual),("regulation",args.regulation)):
        content=path.read_bytes()
        version=docs.upload(content,path.name,role,path.stem,
                            company["id"] if role=="annual_report" else None,
                            args.year if role=="annual_report" else None)
        docs.enqueue_parse(version["id"])
        job=work_once(docs)
        if job["status"]!="succeeded":
            raise RuntimeError(job)
        sources.append({"role":role,"version":version,"parse":job["result"]})
    indexes=Indexes(docs)
    indexes.enqueue([row["parse"]["parse_id"] for row in sources],args.profile,True)
    built=work_once(docs)
    if built["status"]!="succeeded":
        raise RuntimeError(built)
    iid=built["result"]["index_id"]
    search=indexes.search(iid,"审计委员会履职与信息披露义务有哪些依据",args.stock_code,args.year,3,args.reference_root)
    locator_checks=[]
    for role in ("annual_report","regulation"):
        if not search[role]:raise RuntimeError("Missing evidence role: "+role)
        for hit in search[role]:
            source=docs.source(hit["chunk_id"])
            assert source["physical_page"]>=1
            assert source["frame_text"][source["char_start"]:source["char_end"]]==hit["content"]
            assert source["source_available"]
            locator_checks.append({"role":role,"physical_page":source["physical_page"],
                                   "file_sha256":source["file_sha256"],"frame_sha256":source["frame_sha256"],
                                   "chunk_sha256":sha(hit["content"].encode()),"locator_verified":True})
    result={"schema_version":"stage2-real-source-validation-v1","executed_at_utc":datetime.now(timezone.utc).isoformat(),
            "database_profile":"sqlite_diagnostic","retrieval_profile":args.profile,"generation_api_calls":0,
            "documents":[{"role":r["role"],"file_sha256":r["version"]["file_sha256"],"parse":r["parse"]} for r in sources],
            "index":built["result"],"research_parity":search["research_parity"],
            "query_elapsed_seconds":search["elapsed_seconds"],"locator_checks":locator_checks,
            "status":"passed","not_verified":["PostgreSQL runtime","FastAPI runtime","frontend production build"]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
