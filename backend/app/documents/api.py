"""Thin HTTP adapter. Long parsing/indexing operations are persisted as jobs."""
import json
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, File, Form, Header, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from app.documents.common import DomainError
from app.documents.indexes import Indexes
from app.documents.service import Documents
from app.documents.settings import Settings

class StrictModel(BaseModel):
    model_config=ConfigDict(extra="forbid")

class CompanyIn(StrictModel):
    stock_code:str
    stock_name:str=Field(min_length=1,max_length=128)

class ParseIn(StrictModel):
    chunk_chars:int=Field(1200,ge=100,le=8000)
    overlap_chars:int=Field(160,ge=0)
    physical_pages:list[int]|None=None

class IndexIn(StrictModel):
    parse_ids:list[str]=Field(min_length=1,max_length=200)
    profile:Literal["qwen_cuda","bm25_diagnostic"]="qwen_cuda"
    rerank:bool=True

class SearchIn(StrictModel):
    index_id:str
    query:str=Field(min_length=1,max_length=4000)
    stock_code:str
    report_year:int=Field(ge=1990,le=2100)
    top_k:int=Field(5,ge=1,le=20)


class ContextIn(StrictModel):
    index_id:str
    query:str=Field(min_length=1,max_length=4000)
    stock_code:str
    report_year:StrictInt=Field(ge=1990,le=2100)
    annual_top_k:StrictInt=Field(5,ge=1,le=20)
    regulation_top_k:StrictInt=Field(5,ge=1,le=20)
    max_reference_tokens:StrictInt=Field(6000,ge=1,le=32000)
    tokenizer_kind:Literal["qwen3","utf8_diagnostic"]="qwen3"
    temporal_filter:StrictBool=False
    target_date:str|None=None
    target_date_role:Literal["event_date","evaluation_date"]|None=None
    unknown_temporal:Literal["retain","exclude"]="retain"
    max_runtime_seconds:StrictInt=Field(600,ge=1,le=3600)


class EvaluationQuestionIn(StrictModel):
    key:str=Field(min_length=1,max_length=64)
    dimension:Literal["D1","D2","D3","D4"]
    text:str=Field(min_length=1,max_length=4000)
    declared_target_date:str|None=None
    target_date_role:Literal["event_date","evaluation_date"]|None=None


class EvaluationRetrievalIn(StrictModel):
    annual_top_k:StrictInt=Field(5,ge=1,le=20)
    regulation_top_k:StrictInt=Field(5,ge=1,le=20)
    max_reference_tokens:StrictInt=Field(6000,ge=1,le=32000)
    tokenizer_kind:Literal["qwen3","utf8_diagnostic"]="qwen3"
    temporal_filter:StrictBool=False
    unknown_temporal:Literal["retain","exclude"]="retain"


class EvaluationBudgetIn(StrictModel):
    max_model_calls:StrictInt=Field(8,ge=1,le=40)
    max_observed_tokens:StrictInt=Field(80000,ge=1,le=1000000)
    max_output_tokens:StrictInt=Field(8000,ge=128,le=16000)
    max_runtime_seconds:StrictInt=Field(1800,ge=1,le=7200)
    request_timeout_seconds:StrictInt=Field(600,ge=1,le=600)


class EvaluationIn(StrictModel):
    index_id:str
    stock_code:str
    report_year:StrictInt=Field(ge=1990,le=2100)
    question_set_version:Literal["four-dim-v1"]="four-dim-v1"
    questions:list[EvaluationQuestionIn]|None=Field(None,min_length=1,max_length=24)
    retrieval:EvaluationRetrievalIn=Field(default_factory=EvaluationRetrievalIn)
    budget:EvaluationBudgetIn=Field(default_factory=EvaluationBudgetIn)
    model:str|None=Field(None,min_length=1,max_length=160)
    mode:Literal["dual_fixed","dual_agent"]="dual_fixed"
    agent:"AgentIn | None"=None


class AgentIn(StrictModel):
    max_rounds:StrictInt=Field(1,ge=1,le=3)
    max_supplemental_calls:StrictInt=Field(4,ge=0,le=12)
    allow_revision:StrictBool=True


EvaluationIn.model_rebuild()


class ExperimentCaseIn(StrictModel):
    key:str=Field(min_length=1,max_length=64)
    index_id:str
    stock_code:str
    report_year:StrictInt=Field(ge=1990,le=2100)


class ExperimentIn(StrictModel):
    name:str=Field(min_length=1,max_length=200)
    cases:list[ExperimentCaseIn]=Field(min_length=1,max_length=8)
    modes:list[Literal["dual_fixed","dual_agent"]]=Field(default_factory=lambda:["dual_fixed","dual_agent"],min_length=1,max_length=2)
    question_set_version:Literal["four-dim-v1"]="four-dim-v1"
    questions:list[EvaluationQuestionIn]|None=Field(None,min_length=1,max_length=24)
    retrieval:EvaluationRetrievalIn=Field(default_factory=EvaluationRetrievalIn)
    budget:EvaluationBudgetIn=Field(default_factory=lambda:EvaluationBudgetIn(max_model_calls=16))
    agent:AgentIn|None=None
    model:str|None=Field(None,min_length=1,max_length=160)
    max_total_model_calls:StrictInt=Field(128,ge=1,le=128)


class ExperimentRatingIn(StrictModel):
    run_id:str
    answer_id:str
    source_kind:Literal["manual","automatic"]
    rubric_version:str=Field(min_length=1,max_length=80)
    values:dict[str,StrictInt|None]
    reviewer:str=Field(min_length=1,max_length=160)
    method_version:str=Field(min_length=1,max_length=160)
    comment:str=Field("",max_length=4000)
    supersedes_score_id:str|None=None


class AttachRunIn(StrictModel):
    run_id:str
    label:str=Field("",max_length=200)


class HistoryRecordIn(StrictModel):
    record_key:str=Field(min_length=1,max_length=100)
    mode:Literal["dual_fixed","dual_agent","unknown"]
    status:Literal["succeeded","failed","not_run","unknown"]|None=None
    stock_code:str|None=None
    report_year:StrictInt|None=Field(None,ge=1990,le=2100)
    question:str|None=Field(None,max_length=20000)
    answer:str|None=Field(None,max_length=20000)
    notes:str|None=Field(None,max_length=20000)
    quality_scores:dict[str,StrictInt|None]|None=None


class ImportHistoryIn(StrictModel):
    label:str=Field(min_length=1,max_length=200)
    source_identity:str=Field(min_length=1,max_length=500)
    records:list[HistoryRecordIn]=Field(min_length=1,max_length=200)


class ExportIn(StrictModel):
    format:Literal["html","pdf"]="html"


def create_app(settings=None):
    documents=Documents(settings or Settings.from_env())
    indexes=Indexes(documents)
    from app.documents.jobs import JobControl
    from app.retrieval.service import Contexts
    jobs=JobControl(documents)
    contexts=Contexts(documents)
    from app.evaluation.service import Evaluations
    from app.reports.service import Reports
    evaluations=Evaluations(documents)
    reports=Reports(documents)
    from app.experiments.service import Experiments
    experiments=Experiments(documents)
    app=FastAPI(title="上市公司主体自律评价系统",version="0.7.0",
                description="双源评价、受预算约束的Agent、实验对照与可追溯交付；第三章指数结果待接入")
    app.state.documents=documents
    app.state.contexts=contexts
    app.state.evaluations=evaluations
    app.state.reports=reports
    app.state.experiments=experiments

    @app.exception_handler(DomainError)
    async def domain_error(request,error):
        return JSONResponse(status_code=error.status,content={"code":error.code,"message":str(error),"data":None})

    def ok(data):
        return {"code":0,"message":"ok","data":data}

    @app.get("/health/live")
    def live():
        return {"status":"ok"}

    @app.get("/health/ready")
    def ready():
        try:
            if not documents.db.ready():
                raise DomainError("SCHEMA_NOT_READY","请先执行数据库迁移",503)
        except DomainError:
            raise
        except Exception:
            raise DomainError("DATABASE_UNAVAILABLE","数据库未就绪",503)
        import uuid
        probe="health/"+str(uuid.uuid4())+".check"
        try:
            documents.storage.write(probe,b"ready")
            documents.storage.path(probe).unlink()
        except Exception:
            raise DomainError("STORAGE_UNAVAILABLE","持久文件存储不可写",503)
        return {"status":"ok","database_profile":documents.db.dialect,"storage":"writable","schema_revision":5}

    @app.get("/health/workers")
    def healthy_workers():
        from app.documents.worker_health import worker_health
        result=worker_health(documents)
        return JSONResponse(status_code=200 if result["healthy"] else 503,content=result)

    @app.get("/api/v1/companies")
    def companies():
        items=documents.companies()
        return ok({"items":items,"total":len(items)})

    @app.post("/api/v1/companies")
    def company(payload:CompanyIn):
        return ok(documents.company(payload.stock_code,payload.stock_name))

    @app.post("/api/v1/documents/upload")
    async def upload(file:UploadFile=File(...),corpus_type:str=Form(...),title:str=Form(...),
                     company_id:int|None=Form(None),report_year:int|None=Form(None),
                     document_id:str|None=Form(None),version_label:str=Form("1"),metadata:str=Form("{}")):
        try:
            meta=json.loads(metadata)
            if not isinstance(meta,dict):raise ValueError()
        except ValueError:
            raise DomainError("INVALID_METADATA","元数据必须是JSON对象",422)
        data=await file.read(documents.settings.max_upload_bytes+1)
        return ok(documents.upload(data,file.filename or "",corpus_type,title,company_id,report_year,document_id,version_label,meta))

    @app.get("/api/v1/documents")
    def listing(corpus_type:str|None=None,company_id:int|None=None,report_year:int|None=None,
                page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200)):
        return ok(documents.documents(corpus_type,company_id,report_year,page,page_size))

    @app.get("/api/v1/documents/{document_id}/versions")
    def versions(document_id:str):
        return ok(documents.versions(document_id))

    @app.post("/api/v1/document-versions/{version_id}/parse",status_code=202)
    def parse(version_id:str,payload:ParseIn,idempotency_key:str|None=Header(None)):
        return ok(documents.enqueue_parse(version_id,payload.model_dump(),idempotency_key))

    @app.get("/api/v1/document-versions/{version_id}/chunks")
    def chunks(version_id:str,parse_id:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200)):
        return ok(documents.chunks(version_id,parse_id,page,page_size))

    @app.get("/api/v1/document-versions/{version_id}/file")
    def source_file(version_id:str):
        path,filename=documents.file(version_id)
        return FileResponse(path,filename=filename,content_disposition_type="inline")

    @app.get("/api/v1/chunks/{chunk_id}/source")
    def source(chunk_id:str):
        return ok(documents.source(chunk_id))

    @app.post("/api/v1/indexes",status_code=202)
    def index(payload:IndexIn,idempotency_key:str|None=Header(None)):
        return ok(indexes.enqueue(payload.parse_ids,payload.profile,payload.rerank,idempotency_key))

    @app.get("/api/v1/indexes")
    def index_list():
        return ok(indexes.list())

    @app.get("/api/v1/jobs")
    def job_list(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200),status:str|None=None):
        return ok(jobs.list(page,page_size,status))

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id:str):
        return ok(jobs.cancel(job_id))

    @app.post("/api/v1/jobs/{job_id}/retry",status_code=202)
    def retry_job(job_id:str,idempotency_key:str|None=Header(None)):
        return ok(jobs.retry(job_id,idempotency_key))

    @app.post("/api/v1/retrieval/contexts",status_code=202)
    def create_context(payload:ContextIn,idempotency_key:str|None=Header(None)):
        return ok(contexts.enqueue(**payload.model_dump(),key=idempotency_key))

    @app.get("/api/v1/retrieval/contexts")
    def context_list(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200)):
        return ok(contexts.list(page,page_size))

    @app.get("/api/v1/retrieval/contexts/{run_id}")
    def context_run(run_id:str):
        return ok(contexts.get(run_id))

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id:str):
        return ok(documents.job(job_id))

    @app.get("/api/v1/model-profiles")
    def model_profiles():
        from app.evaluation.provider import Provider
        try:
            identity=Provider(documents.settings).identity()
            profile={"name":"openai_compatible","model":identity["model"],"available":True}
        except DomainError as error:
            profile={"name":"openai_compatible","model":None,"available":False,"reason":error.code}
        return ok({"items":[profile],"availability_check":"configuration_only"})

    @app.get("/api/v1/question-sets")
    def question_set_list():
        from app.evaluation.questions import question_sets
        return ok({"items":question_sets()})

    @app.post("/api/v1/evaluations",status_code=202)
    def create_evaluation(payload:EvaluationIn,idempotency_key:str|None=Header(None)):
        return ok(evaluations.enqueue(**payload.model_dump(),key=idempotency_key))

    @app.get("/api/v1/evaluations")
    def evaluation_list(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200),
                        stock_code:str|None=None,report_year:int|None=None,status:str|None=None):
        return ok(evaluations.list(page,page_size,stock_code,report_year,status))

    @app.get("/api/v1/evaluations/{run_id}")
    def evaluation(run_id:str):
        return ok(evaluations.get(run_id))

    @app.get("/api/v1/evaluations/{run_id}/trace")
    def evaluation_trace(run_id:str):
        result=evaluations.get(run_id)
        return ok(result)

    @app.get("/api/v1/evaluations/{run_id}/answers/{answer_id}/evidence")
    def evaluation_evidence(run_id:str,answer_id:str):
        result=evaluations.get(run_id)
        answer=next((a for a in result["answers"] if a["id"]==answer_id),None)
        if answer is None:
            raise DomainError("NOT_FOUND","该运行中不存在指定回答",404)
        return ok(answer)

    @app.post("/api/v1/evaluations/{run_id}/retry",status_code=202)
    def retry_evaluation(run_id:str,idempotency_key:str|None=Header(None)):
        result=evaluations.get(run_id)
        return ok(jobs.retry(result["job_id"],idempotency_key))

    @app.post("/api/v1/evaluations/{run_id}/exports",status_code=201)
    def export_evaluation(run_id:str,payload:ExportIn,idempotency_key:str|None=Header(None)):
        return ok(reports.create(run_id,format=payload.format,key=idempotency_key))

    @app.get("/api/v1/evaluations/{run_id}/exports")
    def evaluation_exports(run_id:str,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200)):
        return ok(reports.list(run_id,page,page_size))

    @app.get("/api/v1/exports/{export_id}")
    def report_export(export_id:str):
        return ok(reports.get(export_id))

    @app.get("/api/v1/exports/{export_id}/file")
    def report_export_file(export_id:str):
        from fastapi.responses import Response
        from urllib.parse import quote
        content,meta=reports.download(export_id)
        disposition="inline" if meta["format"]=="html" else "attachment"
        return Response(content,media_type=meta["media_type"],
                        headers={"Content-Disposition":disposition+"; filename*=UTF-8''"+quote(meta["filename"]),
                                 "X-Content-Type-Options":"nosniff",
                                 "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"})

    @app.post("/api/v1/retrieval/search")
    def search(payload:SearchIn):
        return ok(indexes.search(**payload.model_dump()))

    @app.get("/api/v1/experiments/rubrics")
    def experiment_rubrics():
        return ok(experiments.rubrics())

    @app.post("/api/v1/experiments",status_code=202)
    def experiment_create(payload:ExperimentIn,idempotency_key:str|None=Header(None)):
        return ok(experiments.create(**payload.model_dump(),key=idempotency_key))

    @app.get("/api/v1/experiments")
    def experiment_list(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=200)):
        return ok(experiments.list(page,page_size))

    @app.get("/api/v1/experiments/{experiment_id}")
    def experiment_detail(experiment_id:str):
        return ok(experiments.get(experiment_id))

    @app.post("/api/v1/experiments/{experiment_id}/dispatch",status_code=202)
    def experiment_dispatch(experiment_id:str):
        return ok(experiments.dispatch(experiment_id))

    @app.post("/api/v1/experiments/{experiment_id}/cancel")
    def experiment_cancel(experiment_id:str):
        return ok(experiments.cancel(experiment_id))

    @app.post("/api/v1/experiments/{experiment_id}/ratings",status_code=201)
    def experiment_rate(experiment_id:str,payload:ExperimentRatingIn,idempotency_key:str|None=Header(None)):
        return ok(experiments.add_rating(experiment_id,**payload.model_dump(),key=idempotency_key))

    @app.post("/api/v1/experiments/{experiment_id}/runs",status_code=201)
    def experiment_attach(experiment_id:str,payload:AttachRunIn):
        return ok(experiments.attach(experiment_id,**payload.model_dump()))

    @app.post("/api/v1/experiments/{experiment_id}/history",status_code=201)
    def experiment_import_history(experiment_id:str,payload:ImportHistoryIn,idempotency_key:str|None=Header(None)):
        return ok(experiments.import_history(experiment_id,**payload.model_dump(exclude_none=True),key=idempotency_key))

    @app.get("/api/v1/experiments/{experiment_id}/exports")
    def experiment_export(experiment_id:str,format:Literal["json","csv"]="json"):
        from fastapi.responses import Response
        from urllib.parse import quote
        content,metadata=experiments.export(experiment_id,format=format)
        return Response(content,media_type=metadata["media_type"],headers={
            "Content-Disposition":"attachment; filename*=UTF-8''"+quote(metadata["filename"]),
            "X-Content-Type-Options":"nosniff","X-Snapshot-SHA256":metadata["snapshot_sha256"]})

    return app
