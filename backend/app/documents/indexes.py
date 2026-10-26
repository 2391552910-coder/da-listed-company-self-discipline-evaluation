import json
import os
from pathlib import Path
import subprocess
import sys
import time
from app.documents.common import DomainError, dump, now, sha, uid
from app.documents.service import required
from app.documents.settings import BACKEND

class Indexes:
    def __init__(self, documents):
        self.docs = documents

    def enqueue(self, parse_ids, profile="qwen_cuda", rerank=True, key=None):
        if profile not in {"qwen_cuda","bm25_diagnostic"} or not parse_ids or len(parse_ids)>200:
            raise DomainError("INVALID_INDEX_CONFIG","索引档或解析批次不合法",422)
        parse_ids = sorted(set(parse_ids))
        request = {"parse_ids":parse_ids,"profile":profile,"rerank":bool(rerank) if profile=="qwen_cuda" else False}
        with self.docs.db.session() as db:
            if key:
                old = db.one("SELECT * FROM t_job WHERE idempotency_key=?",(key,))
                if old:
                    return self.docs._enqueue(db,"index",{},key,request)
            for pid in parse_ids:
                row = required(db.one("SELECT status FROM t_parse_run WHERE id=?",(pid,)))
                if row["status"] not in {"done","partial"}:
                    raise DomainError("DOCUMENT_NOT_READY","解析尚未就绪",409)
            index_id = uid()
            db.execute("INSERT INTO t_index_version(id,status,profile,config_json,created_at) VALUES(?,?,?,?,?)",
                       (index_id,"staging",profile,dump(request),now()))
            return self.docs._enqueue(db,"index",request|{"index_id":index_id},key,request)

    def list(self):
        with self.docs.db.session() as db:
            return db.rows("SELECT id,status,profile,created_at,manifest_sha256 FROM t_index_version ORDER BY created_at DESC,id")

    def _dataset(self, parse_ids):
        marks = ",".join("?" for _ in parse_ids)
        with self.docs.db.session() as db:
            rows = db.rows(f"""SELECT c.*,f.text AS frame_text,f.text_sha256 AS frame_sha256,f.physical_page,f.text_origin,
            v.id AS version_id,v.file_sha256,v.storage_key,v.metadata_json,v.source_format,p.config_json AS parse_config_json,p.warnings_json AS parse_warnings_json,d.corpus_type,d.title,d.report_year,
            co.stock_code FROM t_document_chunk c JOIN t_text_frame f ON f.id=c.frame_id
            JOIN t_parse_run p ON p.id=c.parse_id JOIN t_document_version v ON v.id=p.version_id
            JOIN t_document d ON d.id=v.document_id LEFT JOIN t_company co ON co.id=d.company_id
            WHERE c.parse_id IN ({marks}) ORDER BY c.parse_id,c.ordinal""",parse_ids)
        if not rows:
            raise DomainError("EMPTY_INDEX","没有可检索片段",409)
        roles = {r["corpus_type"] for r in rows}
        if roles != {"annual_report","regulation"}:
            raise DomainError("BOTH_CORPORA_REQUIRED","双源索引须同时包含年报和法规",409)
        self._verify_rows(rows)
        return rows

    def _verify_rows(self, rows):
        files = set()
        for row in rows:
            if row["version_id"] not in files:
                self.docs.storage.read(row["storage_key"],row["file_sha256"])
                files.add(row["version_id"])
            if sha(row["frame_text"].encode()) != row["frame_sha256"] or sha(row["content"].encode()) != row["content_sha256"]:
                raise DomainError("LOCATOR_MISMATCH","文本帧或片段完整性校验失败",409)
            if row["frame_text"][row["char_start"]:row["char_end"]] != row["content"]:
                raise DomainError("LOCATOR_MISMATCH","片段不能映射回原文",409)

    def _invoke(self, command, directory, profile, heartbeat=None, extra=()):
        python = self.docs.settings.model_python if profile=="qwen_cuda" else sys.executable
        log = directory / (command+".log")
        env = os.environ.copy()
        env.update(PYTHONUTF8="1",HF_HUB_OFFLINE="1",TOKENIZERS_PARALLELISM="false")
        with log.open("ab") as output:
            try:
                process = subprocess.Popen([python,"-m","app.research_adapter.runner",command,str(directory),*extra],
                    cwd=BACKEND,env=env,stdout=output,stderr=subprocess.STDOUT)
            except OSError:
                raise DomainError("MODEL_PROFILE_UNAVAILABLE","模型Python不可用，请检查运行配置",503)
            started, last_beat = time.monotonic(), 0
            try:
                while process.poll() is None:
                    if time.monotonic()-started > self.docs.settings.model_timeout:
                        raise DomainError("MODEL_TIMEOUT","模型执行超时",503)
                    if heartbeat and time.monotonic()-last_beat > 20:
                        heartbeat()
                        last_beat=time.monotonic()
                    time.sleep(.2)
            except BaseException:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait()
                raise
            if process.returncode:
                raise DomainError("MODEL_EXECUTION_FAILED","模型执行失败，详见运行环境中的任务日志",503)

    def build(self, payload, heartbeat=None):
        index_id=payload["index_id"]
        base=f"indexes/{index_id}"
        directory=self.docs.storage.path(base)
        directory.mkdir(parents=True,exist_ok=True)
        rows=self._dataset(payload["parse_ids"])
        # On a recovered attempt, reuse only an independently verified complete artifact.
        self.docs.storage.write(base+"/dataset.json",dump({"rows":rows,"config":payload}).encode())
        try:
            self._invoke("build",directory,payload["profile"],heartbeat)
            manifest_bytes=self.docs.storage.read(base+"/manifest.json")
            manifest=json.loads(manifest_bytes)
            self.docs.storage.read(base+"/dataset.json",manifest["dataset_sha256"])
            if manifest["profile"]!=payload["profile"]:
                raise DomainError("MODEL_IDENTITY_MISMATCH","索引运行档不匹配",409)
            with self.docs.db.session() as db:
                for ordinal,row in enumerate(rows):
                    db.execute("INSERT INTO t_index_member(index_id,chunk_id,ordinal) VALUES(?,?,?) ON CONFLICT(index_id,chunk_id) DO NOTHING",
                               (index_id,row["id"],ordinal))
                db.execute("UPDATE t_index_version SET status='ready',manifest_key=?,manifest_sha256=? WHERE id=?",
                           (base+"/manifest.json",sha(manifest_bytes),index_id))
            return {"index_id":index_id,"profile":payload["profile"],"chunk_count":len(rows),
                    "manifest_sha256":sha(manifest_bytes),"encoder_identity":manifest["encoder_identity"]}
        except Exception:
            with self.docs.db.session() as db:
                db.execute("UPDATE t_index_version SET status='failed' WHERE id=? AND status!='ready'",(index_id,))
            raise

    def validate(self,index_id):
        with self.docs.db.session() as db:
            index=required(db.one("SELECT * FROM t_index_version WHERE id=?",(index_id,)))
            members=db.rows("SELECT chunk_id FROM t_index_member WHERE index_id=? ORDER BY ordinal",(index_id,))
        if index["status"]!="ready":
            raise DomainError("INDEX_NOT_READY","索引尚未就绪",409)
        manifest=json.loads(self.docs.storage.read(index["manifest_key"],index["manifest_sha256"]))
        base=str(Path(index["manifest_key"]).parent).replace("\\","/")
        dataset=json.loads(self.docs.storage.read(base+"/dataset.json",manifest["dataset_sha256"]))
        if [r["id"] for r in dataset["rows"]]!=[r["chunk_id"] for r in members]:
            raise DomainError("INDEX_MEMBER_MISMATCH","索引片段映射发生变化",409)
        self._verify_rows(dataset["rows"])
        if manifest["profile"]!=index["profile"]:
            raise DomainError("MODEL_IDENTITY_MISMATCH","索引身份发生变化",409)
        if manifest.get("vector_sha256"):
            self.docs.storage.read(base+"/vectors.npy",manifest["vector_sha256"])
        return index,manifest,dataset,base

    def search(self,index_id,query,stock_code,report_year,top_k=5,reference_root=None):
        if not query.strip() or len(query)>4000 or not 1<=top_k<=20:
            raise DomainError("INVALID_QUERY","查询为空、过长或top_k超限",422)
        from app.documents.common import code
        stock_code=code(stock_code)
        index,manifest,dataset,base=self.validate(index_id)
        if not any(r["corpus_type"]=="annual_report" and r["stock_code"]==stock_code and r["report_year"]==report_year for r in dataset["rows"]):
            raise DomainError("ANNUAL_SCOPE_EMPTY","索引中没有指定公司年度年报",409)
        request_id=uid()
        request_key=f"{base}/queries/{request_id}.json"
        response_key=f"{base}/queries/{request_id}.result.json"
        self.docs.storage.write(request_key,dump({"query":query,"stock_code":stock_code,"report_year":report_year,"top_k":top_k}).encode())
        extra=[str(self.docs.storage.path(request_key)),str(self.docs.storage.path(response_key))]
        if reference_root:
            # CLI-only trusted configuration, never accepted from HTTP input.
            extra += ["--reference-root",str(Path(reference_root).resolve())]
        self._invoke("query",self.docs.storage.path(base),index["profile"],extra=extra)
        result=json.loads(self.docs.storage.read(response_key))
        # Validate again after the model call; changed sources must not be served.
        self.validate(index_id)
        by_id={r["id"]:r for r in dataset["rows"]}
        for role in ("annual_report","regulation"):
            for hit in result[role]:
                row=by_id.get(hit["chunk_id"])
                if not row or row["corpus_type"]!=role or (role=="annual_report" and (row["stock_code"]!=stock_code or row["report_year"]!=report_year)):
                    raise DomainError("RETRIEVAL_SCOPE_VIOLATION","检索结果越过指定作用域",409)
                hit.update(content=row["content"],title=row["title"],version_id=row["version_id"],
                           physical_page=row["physical_page"],char_start=row["char_start"],char_end=row["char_end"],
                           source_format=row["source_format"],parse_warnings=json.loads(row["parse_warnings_json"]),source_url=f"/api/v1/chunks/{row['id']}/source")
        result.update(index_id=index_id,profile=index["profile"],manifest_sha256=index["manifest_sha256"],
                      warning="仅诊断BM25，未使用稠密模型" if index["profile"]=="bm25_diagnostic" else None)
        return result
