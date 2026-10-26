"""Queued dual-source retrieval, whole-chunk budgets and immutable replay."""
import json
import time
from app.documents.common import DomainError,code,dump,now,sha,uid
from app.documents.indexes import Indexes
from app.documents.service import required
from app.retrieval.context_builder import assemble_context
from app.retrieval.temporal import select_regulations
from app.retrieval.token_budget import load_counter

def fence_job(db,job_id,worker_id):
    moment=now()
    changed=db.execute("""UPDATE t_job SET updated_at=? WHERE id=? AND status='running'
        AND lease_owner=? AND cancel_requested=0 AND lease_until>=?""",
        (moment,job_id,worker_id,moment)).rowcount
    if changed!=1:
        row=db.one("SELECT cancel_requested FROM t_job WHERE id=?",(job_id,))
        if row and row["cancel_requested"]:
            raise DomainError("TASK_CANCELLED","任务已请求取消",409)
        raise DomainError("LEASE_LOST","发布时任务租约已丢失",409)

class Contexts:
    def __init__(self,documents):
        self.docs=documents
        self.indexes=Indexes(documents)

    def enqueue(self,index_id,query,stock_code,report_year,annual_top_k=5,regulation_top_k=5,
                max_reference_tokens=6000,tokenizer_kind="qwen3",temporal_filter=False,target_date=None,
                target_date_role=None,unknown_temporal="retain",max_runtime_seconds=600,key=None):
        for value,minimum,maximum,name in (
            (annual_top_k,1,20,"annual_top_k"),(regulation_top_k,1,20,"regulation_top_k"),
            (max_reference_tokens,1,32000,"max_reference_tokens"),(report_year,1990,2100,"report_year"),
            (max_runtime_seconds,1,3600,"max_runtime_seconds"),
        ):
            if type(value) is not int or not minimum<=value<=maximum:
                raise DomainError("INVALID_RETRIEVAL_CONFIG",name+"不合法",422)
        if not isinstance(query,str) or not query.strip() or len(query)>4000:
            raise DomainError("INVALID_QUERY","问题不能为空或超过4000字符",422)
        try:
            query.encode("utf-8")
        except UnicodeError:
            raise DomainError("INVALID_QUERY","问题包含无效Unicode字符",422)
        if key is not None and (not isinstance(key,str) or not key.strip() or len(key)>200):
            raise DomainError("INVALID_IDEMPOTENCY_KEY","幂等键为空或过长",422)
        stock_code=code(stock_code)
        select_regulations([],temporal_filter=temporal_filter,target_date=target_date,
                           target_date_role=target_date_role,unknown_policy=unknown_temporal)
        public_request={"index_id":index_id,"query":query.strip(),"stock_code":stock_code,"report_year":report_year,
                 "annual_top_k":annual_top_k,"regulation_top_k":regulation_top_k,
                 "max_reference_tokens":max_reference_tokens,"tokenizer_kind":tokenizer_kind,
                 "temporal_filter":temporal_filter,"target_date":target_date,"target_date_role":target_date_role,
                 "unknown_temporal":unknown_temporal,"max_runtime_seconds":max_runtime_seconds}
        if key:
            with self.docs.db.session() as db:
                old=db.one("SELECT * FROM t_job WHERE idempotency_key=?",(key,))
                if old:
                    return self.docs._enqueue(db,"context",{},key,public_request)
        _,tokenizer_identity=load_counter(tokenizer_kind,self.docs.settings)
        index,_,dataset,_=self.indexes.validate(index_id)
        if not any(r["corpus_type"]=="annual_report" and r["stock_code"]==stock_code and r["report_year"]==report_year for r in dataset["rows"]):
            raise DomainError("ANNUAL_SCOPE_EMPTY","指定公司年度没有年报候选",409)
        request=public_request|{"index_manifest_sha256":index["manifest_sha256"],"tokenizer_identity":tokenizer_identity}
        return self._commit_request(request,public_request,key)

    def _commit_request(self,request,public_request,key):
        try:
            with self.docs.db.session() as db:
                if key:
                    old=db.one("SELECT * FROM t_job WHERE idempotency_key=?",(key,))
                    if old:
                        return self.docs._enqueue(db,"context",{},key,public_request)
                run_id=self.new_run(db,request)
                task=self.docs._enqueue(db,"context",{"run_id":run_id,"request":request},key,public_request)
                db.execute("UPDATE t_retrieval_run SET job_id=? WHERE id=?",(task["id"],run_id))
                return task|{"run_id":run_id}
        except DomainError:
            raise
        except Exception as error:
            sqlstate=getattr(error,"sqlstate",None) or getattr(getattr(error,"orig",None),"sqlstate",None)
            unique=sqlstate=="23505" or (error.__class__.__name__=="IntegrityError" and "UNIQUE" in str(error).upper())
            if key and unique:
                with self.docs.db.session() as db:
                    old=db.one("SELECT * FROM t_job WHERE idempotency_key=?",(key,))
                    if old:
                        return self.docs._enqueue(db,"context",{},key,public_request)
            raise

    @staticmethod
    def new_run(db,request,parent_run_id=None):
        run_id=uid()
        db.execute("""INSERT INTO t_retrieval_run
          (id,index_id,request_json,request_sha256,status,parent_run_id,created_at) VALUES(?,?,?,?,?,?,?)""",
          (run_id,request["index_id"],dump(request),sha(dump(request).encode()),"queued",parent_run_id,now()))
        return run_id

    def build(self,payload,job_id,worker_id,heartbeat):
        run_id,request=payload["run_id"],payload["request"]
        with self.docs.db.session() as db:
            run=required(db.one("SELECT * FROM t_retrieval_run WHERE id=? AND job_id=?",(run_id,job_id)))
            attempt=required(db.one("SELECT attempt_count FROM t_job WHERE id=?",(job_id,)))["attempt_count"]
        if sha(dump(request).encode())!=run["request_sha256"] or dump(request)!=run["request_json"]:
            raise DomainError("REQUEST_SNAPSHOT_MISMATCH","检索请求快照已变化",409)
        started=time.monotonic()
        def checkpoint(stage="retrieving"):
            heartbeat()
            if time.monotonic()-started>=request["max_runtime_seconds"]:
                raise DomainError("BUDGET_EXHAUSTED","检索任务运行时间预算耗尽",409)
            with self.docs.db.session() as db:
                db.execute("UPDATE t_job SET progress_json=? WHERE id=? AND status='running' AND lease_owner=?",
                           (dump({"stage":stage,"elapsed_seconds":round(time.monotonic()-started,3)}),job_id,worker_id))
        checkpoint("validating")
        current,_,_,_=self.indexes.validate(request["index_id"])
        if current["manifest_sha256"]!=request["index_manifest_sha256"]:
            raise DomainError("INDEX_SNAPSHOT_MISMATCH","索引版本清单已变化",409)
        counter,identity=load_counter(request["tokenizer_kind"],self.docs.settings,request["tokenizer_identity"])
        if run["snapshot_key"]:
            snapshot=self._decode_snapshot(run)
            context=snapshot["context"]
            if counter(context["context_text"])!=context["reference_token_count"] or context["reference_token_count"]>request["max_reference_tokens"]:
                raise DomainError("CONTEXT_SNAPSHOT_MISMATCH","恢复快照的实际计数与冻结预算不同",409)
            checkpoint("replaying")
            with self.docs.db.session() as db:
                fence_job(db,job_id,worker_id)
            return self._result(run_id,run["snapshot_sha256"],context,snapshot["attempt_number"],True)
        retrieval=self.indexes.search(
            request["index_id"],request["query"],request["stock_code"],request["report_year"],
            annual_top_k=request["annual_top_k"],regulation_top_k=request["regulation_top_k"],
            temporal_filter=request["temporal_filter"],target_date=request["target_date"],
            target_date_role=request["target_date_role"],unknown_temporal=request["unknown_temporal"],
            heartbeat=lambda:checkpoint("retrieving"),deadline=started+request["max_runtime_seconds"])
        checkpoint("assembling")
        context=assemble_context({role:retrieval[role] for role in ("annual_report","regulation")},
                                 request["max_reference_tokens"],counter,identity)
        context["budget_unit"]=identity["budget_unit"]
        snapshot={"schema_version":"retrieval-context-snapshot-v1","run_id":run_id,"job_id":job_id,
                  "attempt_number":attempt,"request":request,"request_sha256":run["request_sha256"],
                  "retrieval":retrieval,"context":context,"semantic_support_verified":False}
        data=dump(snapshot).encode()
        digest=sha(data)
        key=f"contexts/{run_id}/attempt-{attempt}/{digest}.json"
        self.docs.storage.write(key,data)
        checkpoint("persisting")
        with self.docs.db.session() as db:
            fence_job(db,job_id,worker_id)
            changed=db.execute("UPDATE t_retrieval_run SET snapshot_key=?,snapshot_sha256=? WHERE id=? AND job_id=?",
                              (key,digest,run_id,job_id)).rowcount
            if changed!=1:
                raise DomainError("CONTEXT_RUN_MISMATCH","快照与任务身份不一致",409)
        return self._result(run_id,digest,context,attempt,False)

    @staticmethod
    def _result(run_id,digest,context,origin,recovered):
        exhausted=context["diagnostics"]["budget_excluded_all"]
        return {"run_id":run_id,"status":"budget_exhausted" if exhausted else "succeeded",
                "snapshot_sha256":digest,"reference_token_count":context["reference_token_count"],
                "budget_unit":context["tokenizer_identity"]["budget_unit"],"evidence_count":len(context["source_records"]),
                "evidence_status":"insufficient" if context["diagnostics"]["missing_context_roles"] else "dual_source",
                "error_code":"BUDGET_EXHAUSTED" if exhausted else None,"snapshot_origin_attempt":origin,
                "recovered_snapshot":recovered}

    def _decode_snapshot(self,row):
        try:
            snapshot=json.loads(self.docs.storage.read(row["snapshot_key"],row["snapshot_sha256"]))
            context=snapshot["context"]
            if (snapshot["run_id"]!=row["id"] or snapshot["job_id"]!=row["job_id"]
                or snapshot["request_sha256"]!=row["request_sha256"]
                or dump(snapshot["request"])!=row["request_json"]
                or sha(context["context_text"].encode())!=context["context_sha256"]
                or sha(dump(context["source_records"]).encode())!=context["source_records_sha256"]
                or dump(context["tokenizer_identity"])!=dump(snapshot["request"]["tokenizer_identity"])
                or type(snapshot["attempt_number"]) is not int or snapshot["attempt_number"]<1):
                raise ValueError("snapshot identity mismatch")
            return snapshot
        except DomainError:
            raise
        except (KeyError,ValueError,TypeError,UnicodeError) as error:
            raise DomainError("CONTEXT_SNAPSHOT_MISMATCH","上下文快照或来源记录不匹配",409) from error

    def get(self,run_id):
        with self.docs.db.session() as db:
            row=required(db.one("SELECT * FROM t_retrieval_run WHERE id=?",(run_id,)))
            job=required(db.one("SELECT * FROM t_job WHERE id=?",(row["job_id"],)))
        result={k:row[k] for k in ("id","job_id","index_id","status","snapshot_sha256","parent_run_id","created_at")}
        request=json.loads(row["request_json"])
        if sha(dump(request).encode())!=row["request_sha256"]:
            raise DomainError("REQUEST_SNAPSHOT_MISMATCH","检索运行请求哈希不符",409)
        result["request"]=request
        result["snapshot"]=None
        job_result=json.loads(job["result_json"]) if job["result_json"] else {}
        if row["status"]=="succeeded" and (not row["snapshot_key"] or not job_result.get("snapshot_sha256")):
            raise DomainError("CONTEXT_JOB_MISMATCH","成功任务缺少最终快照提交证明",409)
        if row["status"] in {"succeeded","budget_exhausted"} and row["snapshot_key"]:
            if (job_result.get("snapshot_sha256")!=row["snapshot_sha256"] or job["status"]!=row["status"]
                or job_result.get("run_id")!=run_id):
                raise DomainError("CONTEXT_JOB_MISMATCH","运行快照与最终任务结果不一致",409)
            snapshot=self._decode_snapshot(row)
            origin=job_result.get("snapshot_origin_attempt")
            if origin!=snapshot["attempt_number"] or type(origin) is not int or not 1<=origin<=job["attempt_count"]:
                raise DomainError("CONTEXT_JOB_MISMATCH","快照与任务执行账本不一致",409)
            result["snapshot"]=snapshot
        return result

    def list(self,page=1,size=20):
        if type(page) is not int or page<1 or type(size) is not int or not 1<=size<=200:
            raise DomainError("INVALID_PAGE","分页参数不合法",422)
        with self.docs.db.session() as db:
            total=db.one("SELECT COUNT(*) AS n FROM t_retrieval_run")["n"]
            rows=db.rows("""SELECT id,job_id,index_id,status,snapshot_sha256,parent_run_id,created_at
              FROM t_retrieval_run ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",(size,(page-1)*size))
        return {"items":rows,"total":total,"page":page,"page_size":size}
