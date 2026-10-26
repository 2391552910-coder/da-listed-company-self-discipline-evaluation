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


def create_app(settings=None):
    documents=Documents(settings or Settings.from_env())
    indexes=Indexes(documents)
    from app.documents.jobs import JobControl
    from app.retrieval.service import Contexts
    jobs=JobControl(documents)
    contexts=Contexts(documents)
    app=FastAPI(title="上市公司主体自律评价系统",version="0.3.0",
                description="阶段3：双源检索、冻结上下文与可恢复任务；生成评价及第三章结果待接入")
    app.state.documents=documents
    app.state.contexts=contexts

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
        return {"status":"ok","database_profile":documents.db.dialect}

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

    @app.post("/api/v1/retrieval/search")
    def search(payload:SearchIn):
        return ok(indexes.search(**payload.model_dump()))

    return app
