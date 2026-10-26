"""Local/server command interface over the same services as the HTTP adapter."""
import argparse
import json
from pathlib import Path
from app.documents.common import DomainError
from app.documents.indexes import Indexes
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import run

def main(argv=None):
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("migrate")
    company=sub.add_parser("company")
    company.add_argument("stock_code");company.add_argument("stock_name")
    upload=sub.add_parser("upload")
    upload.add_argument("file",type=Path);upload.add_argument("--corpus",choices=["annual_report","regulation"],required=True)
    upload.add_argument("--title",required=True);upload.add_argument("--company-id",type=int);upload.add_argument("--year",type=int)
    upload.add_argument("--document-id");upload.add_argument("--version-label",default="1");upload.add_argument("--metadata",default="{}")
    sub.add_parser("documents")
    versions=sub.add_parser("versions");versions.add_argument("document_id")
    parse=sub.add_parser("parse");parse.add_argument("version_id");parse.add_argument("--physical-pages",type=int,nargs="+")
    parse.add_argument("--key")
    worker=sub.add_parser("worker");worker.add_argument("--once",action="store_true")
    index=sub.add_parser("index");index.add_argument("parse_ids",nargs="+");index.add_argument("--profile",choices=["qwen_cuda","bm25_diagnostic"],default="qwen_cuda")
    index.add_argument("--no-rerank",action="store_true");index.add_argument("--key")
    sub.add_parser("indexes")
    search=sub.add_parser("search");search.add_argument("index_id");search.add_argument("query")
    search.add_argument("--stock-code",required=True);search.add_argument("--year",type=int,required=True);search.add_argument("--top-k",type=int,default=5)
    search.add_argument("--reference-root",type=Path)
    job=sub.add_parser("job");job.add_argument("job_id")
    source=sub.add_parser("source");source.add_argument("chunk_id")
    context=sub.add_parser("context")
    context.add_argument("index_id");context.add_argument("query")
    context.add_argument("--stock-code",required=True);context.add_argument("--year",type=int,required=True)
    context.add_argument("--annual-top-k",type=int,default=5);context.add_argument("--regulation-top-k",type=int,default=5)
    context.add_argument("--max-reference-tokens",type=int,default=6000)
    context.add_argument("--tokenizer-kind",choices=["qwen3","utf8_diagnostic"],default="qwen3")
    context.add_argument("--temporal-filter",action="store_true");context.add_argument("--target-date")
    context.add_argument("--target-date-role",choices=["event_date","evaluation_date"])
    context.add_argument("--unknown-temporal",choices=["retain","exclude"],default="retain")
    context.add_argument("--max-runtime-seconds",type=int,default=600);context.add_argument("--key")
    show_context=sub.add_parser("show-context");show_context.add_argument("run_id")
    sub.add_parser("contexts");sub.add_parser("jobs")
    cancel=sub.add_parser("cancel");cancel.add_argument("job_id")
    retry=sub.add_parser("retry");retry.add_argument("job_id");retry.add_argument("--key")
    sub.add_parser("question-sets")
    evaluation=sub.add_parser("evaluate")
    evaluation.add_argument("request_file",type=Path);evaluation.add_argument("--key")
    sub.add_parser("evaluations")
    show_evaluation=sub.add_parser("show-evaluation");show_evaluation.add_argument("run_id")
    export=sub.add_parser("export")
    export.add_argument("run_id");export.add_argument("--format",choices=["html","pdf"],default="html");export.add_argument("--key")
    sub.add_parser("experiments")
    experiment=sub.add_parser("create-experiment");experiment.add_argument("request_file",type=Path);experiment.add_argument("--key")
    for name in ("show-experiment","dispatch-experiment","cancel-experiment"):
        command=sub.add_parser(name);command.add_argument("experiment_id")
    for name in ("rate-experiment","import-experiment-history"):
        command=sub.add_parser(name);command.add_argument("experiment_id");command.add_argument("request_file",type=Path);command.add_argument("--key")
    attach=sub.add_parser("attach-experiment-run");attach.add_argument("experiment_id");attach.add_argument("run_id");attach.add_argument("--label",default="")
    export_experiment=sub.add_parser("export-experiment");export_experiment.add_argument("experiment_id")
    export_experiment.add_argument("--format",choices=["json","csv"],default="json");export_experiment.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    docs=Documents(Settings.from_env());indexes=Indexes(docs)
    from app.documents.jobs import JobControl
    from app.retrieval.service import Contexts
    jobs=JobControl(docs);contexts=Contexts(docs)
    try:
        if args.command=="migrate":docs.db.migrate();result={"status":"migrated","dialect":docs.db.dialect}
        elif args.command=="company":result=docs.company(args.stock_code,args.stock_name)
        elif args.command=="upload":result=docs.upload(args.file.read_bytes(),args.file.name,args.corpus,args.title,args.company_id,args.year,args.document_id,args.version_label,json.loads(args.metadata))
        elif args.command=="documents":result=docs.documents()
        elif args.command=="versions":result=docs.versions(args.document_id)
        elif args.command=="parse":result=docs.enqueue_parse(args.version_id,{"physical_pages":args.physical_pages},args.key)
        elif args.command=="worker":result=run(docs,args.once)
        elif args.command=="index":result=indexes.enqueue(args.parse_ids,args.profile,not args.no_rerank,args.key)
        elif args.command=="indexes":result=indexes.list()
        elif args.command=="search":result=indexes.search(args.index_id,args.query,args.stock_code,args.year,args.top_k,args.reference_root)
        elif args.command=="context":result=contexts.enqueue(args.index_id,args.query,args.stock_code,args.year,
            args.annual_top_k,args.regulation_top_k,args.max_reference_tokens,args.tokenizer_kind,
            args.temporal_filter,args.target_date,args.target_date_role,args.unknown_temporal,args.max_runtime_seconds,args.key)
        elif args.command=="show-context":result=contexts.get(args.run_id)
        elif args.command=="contexts":result=contexts.list()
        elif args.command=="jobs":result=jobs.list()
        elif args.command=="cancel":result=jobs.cancel(args.job_id)
        elif args.command=="retry":result=jobs.retry(args.job_id,args.key)
        elif args.command=="job":result=docs.job(args.job_id)
        elif args.command=="source":result=docs.source(args.chunk_id)
        elif args.command=="question-sets":
            from app.evaluation.questions import question_sets
            result={"items":question_sets()}
        elif args.command in {"evaluate","evaluations","show-evaluation"}:
            from app.evaluation.service import Evaluations
            evaluations=Evaluations(docs)
            if args.command=="evaluate":result=evaluations.enqueue(**json.loads(args.request_file.read_text(encoding="utf-8")),key=args.key)
            elif args.command=="evaluations":result=evaluations.list()
            else:result=evaluations.get(args.run_id)
        elif args.command=="export":
            from app.reports.service import Reports
            result=Reports(docs).create(args.run_id,format=args.format,key=args.key)
        elif "experiment" in args.command:
            from app.experiments.service import Experiments
            experiments=Experiments(docs)
            if args.command=="experiments":result=experiments.list()
            elif args.command=="create-experiment":result=experiments.create(**json.loads(args.request_file.read_text(encoding="utf-8")),key=args.key)
            elif args.command=="show-experiment":result=experiments.get(args.experiment_id)
            elif args.command=="dispatch-experiment":result=experiments.dispatch(args.experiment_id)
            elif args.command=="cancel-experiment":result=experiments.cancel(args.experiment_id)
            elif args.command=="rate-experiment":result=experiments.add_rating(args.experiment_id,**json.loads(args.request_file.read_text(encoding="utf-8")),key=args.key)
            elif args.command=="import-experiment-history":result=experiments.import_history(args.experiment_id,**json.loads(args.request_file.read_text(encoding="utf-8")),key=args.key)
            elif args.command=="attach-experiment-run":result=experiments.attach(args.experiment_id,args.run_id,args.label)
            else:
                content,result=experiments.export(args.experiment_id,args.format)
                if args.output.exists():raise DomainError("OUTPUT_EXISTS","输出文件已存在，请选择新文件",409)
                args.output.parent.mkdir(parents=True,exist_ok=True)
                with args.output.open("xb") as destination:destination.write(content)
                result=result|{"saved":True}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except DomainError as error:
        print(json.dumps({"code":error.code,"message":str(error)},ensure_ascii=False))
        return 1

if __name__=="__main__":
    raise SystemExit(main())
