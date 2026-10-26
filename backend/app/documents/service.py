from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
from app.documents.common import DomainError, code, dump, now, sha, uid
from app.documents.database import Database
from app.documents.parsing import DEFAULT_CONFIG, check_annual_identity, parse_file
from app.documents.storage import Storage

def required(row):
    if row is None:
        raise DomainError("NOT_FOUND", "记录不存在", 404)
    return row

class Documents:
    def __init__(self, settings):
        self.settings = settings
        self.db = Database(settings.database_url)
        self.storage = Storage(settings.data_root)

    def company(self, stock_code, stock_name):
        stock_code = code(stock_code)
        if not stock_name.strip():
            raise DomainError("INVALID_COMPANY_NAME", "公司名称不能为空", 422)
        with self.db.session() as db:
            old = db.one("SELECT * FROM t_company WHERE stock_code=?", (stock_code,))
            if old:
                return old
            db.execute("INSERT INTO t_company(stock_code,stock_name,created_at) VALUES(?,?,?)",
                       (stock_code, stock_name.strip(), now()))
            return db.one("SELECT * FROM t_company WHERE stock_code=?", (stock_code,))

    def companies(self):
        with self.db.session() as db:
            return db.rows("SELECT id,stock_code,stock_name FROM t_company ORDER BY stock_code")

    def upload(self, data, filename, corpus_type, title, company_id=None, report_year=None,
               document_id=None, version_label="1", metadata=None):
        extension = Path(filename).suffix.lower().lstrip(".")
        if not data or len(data) > self.settings.max_upload_bytes:
            raise DomainError("UPLOAD_SIZE", "文件为空或超过大小限制", 413)
        if extension not in {"pdf", "txt", "docx"} or corpus_type not in {"annual_report", "regulation"}:
            raise DomainError("UNSUPPORTED_FORMAT", "仅支持PDF/TXT/DOCX及年报/法规来源", 422)
        if corpus_type == "annual_report" and (extension != "pdf" or not company_id or not report_year):
            raise DomainError("ANNUAL_METADATA_REQUIRED", "年报需PDF文件、公司及年度", 422)
        if extension == "pdf" and not data[:1024].lstrip().startswith(b"%PDF-"):
            raise DomainError("INVALID_PDF", "文件内容不是PDF", 422)
        if corpus_type == "regulation" and (company_id is not None or report_year is not None):
            raise DomainError("REGULATION_SCOPE_INVALID", "法规不能绑定公司年度", 422)
        metadata = dict(metadata or {})
        for field in ("publication_date", "effective_from", "effective_to_exclusive", "metadata_as_of_date"):
            if metadata.get(field):
                try:
                    date.fromisoformat(metadata[field])
                except (ValueError, TypeError):
                    raise DomainError("INVALID_DATE", f"{field}须为ISO日期", 422)
        if metadata.get("effective_from") and metadata.get("effective_to_exclusive"):
            if metadata["effective_from"] >= metadata["effective_to_exclusive"]:
                raise DomainError("INVALID_DATE_INTERVAL", "有效期结束须晚于开始", 422)
        metadata["identity_status"] = "declared_unverified"
        metadata["applicability_status"] = "metadata_unverified"
        metadata.setdefault("interval_end_status", "unknown")
        if not title.strip() or not version_label.strip():
            raise DomainError("MISSING_TITLE", "文档标题及版本不能为空", 422)
        version_id, digest = uid(), sha(data)
        with self.db.session() as db:
            if company_id:
                required(db.one("SELECT id FROM t_company WHERE id=?", (company_id,)))
            if document_id:
                document = required(db.one("SELECT * FROM t_document WHERE id=?", (document_id,)))
                if (document["corpus_type"], document["company_id"], document["report_year"]) != (corpus_type, company_id, report_year):
                    raise DomainError("DOCUMENT_SCOPE_CONFLICT", "文档身份与新版本不一致", 409)
            else:
                document = db.one("SELECT * FROM t_document WHERE company_id=? AND report_year=? AND corpus_type='annual_report'",
                                  (company_id, report_year)) if corpus_type == "annual_report" else None
                if not document:
                    document = {"id": uid()}
                    db.execute("INSERT INTO t_document(id,corpus_type,company_id,report_year,title,created_at) VALUES(?,?,?,?,?,?)",
                               (document["id"], corpus_type, company_id, report_year, title.strip(), now()))
            existing = db.one("SELECT * FROM t_document_version WHERE document_id=? AND file_sha256=?",
                              (document["id"], digest))
            if existing:
                return self.public_version(existing, duplicate=True)
            key = f"sources/{version_id}/original.{extension}"
            self.storage.write(key, data)
            db.execute("""INSERT INTO t_document_version
            (id,document_id,version_label,filename,storage_key,file_sha256,source_format,metadata_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
                       (version_id, document["id"], version_label, Path(filename).name, key, digest, extension, dump(metadata), now()))
            return self.public_version(db.one("SELECT * FROM t_document_version WHERE id=?", (version_id,)))

    @staticmethod
    def public_version(row, duplicate=False):
        row = dict(row)
        row.pop("storage_key", None)
        row["metadata"] = json.loads(row.pop("metadata_json"))
        row["duplicate"] = duplicate
        return row

    def documents(self, corpus_type=None, company_id=None, report_year=None, page=1, size=20):
        if page < 1 or not 1 <= size <= 200:
            raise DomainError("INVALID_PAGE", "分页参数不合法", 422)
        where, args = [], []
        for key, value in (("corpus_type", corpus_type), ("company_id", company_id), ("report_year", report_year)):
            if value is not None:
                where.append(f"d.{key}=?")
                args.append(value)
        condition = " WHERE " + " AND ".join(where) if where else ""
        with self.db.session() as db:
            count = db.one("SELECT COUNT(*) AS n FROM t_document d" + condition, args)["n"]
            rows = db.rows("SELECT d.* FROM t_document d" + condition + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                           (*args, size, (page-1)*size))
        return {"items": rows, "total": count, "page": page, "page_size": size}

    def versions(self, document_id):
        with self.db.session() as db:
            required(db.one("SELECT id FROM t_document WHERE id=?", (document_id,)))
            rows = db.rows("SELECT * FROM t_document_version WHERE document_id=? ORDER BY created_at DESC,id", (document_id,))
            result = []
            for row in rows:
                item = self.public_version(row)
                item["parse_runs"] = db.rows("SELECT id,status,parser_identity,warnings_json,error_code FROM t_parse_run WHERE version_id=? ORDER BY created_at DESC,id", (row["id"],))
                result.append(item)
            return result

    def _enqueue(self, db, kind, payload, key=None, request=None):
        digest = sha(dump(request or payload).encode())
        if key:
            old = db.one("SELECT * FROM t_job WHERE idempotency_key=?", (key,))
            if old:
                if old["request_sha256"] != digest or old["kind"] != kind:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "相同幂等键对应不同请求", 409)
                return self.public_job(old)
        job_id = uid()
        db.execute("INSERT INTO t_job(id,kind,status,payload_json,idempotency_key,request_sha256,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   (job_id, kind, "queued", dump(payload), key, digest, now(), now()))
        return self.public_job(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))

    def enqueue_parse(self, version_id, config=None, key=None):
        config = {**DEFAULT_CONFIG, **(config or {})}
        if not 100 <= config["chunk_chars"] <= 8000 or not 0 <= config["overlap_chars"] < config["chunk_chars"]:
            raise DomainError("INVALID_CHUNK_CONFIG", "分块配置不合法", 422)
        with self.db.session() as db:
            required(db.one("SELECT id FROM t_document_version WHERE id=?", (version_id,)))
            return self._enqueue(db, "parse", {"version_id": version_id, "config": config}, key)

    @staticmethod
    def public_job(row):
        return {key: row[key] for key in ("id","kind","status","error_code","attempt_count","created_at","updated_at")} | {
            "result": json.loads(row["result_json"]) if row.get("result_json") else None}

    def job(self, job_id):
        with self.db.session() as db:
            return self.public_job(required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,))))

    def parse(self, version_id, config):
        parse_id = uid()
        with self.db.session() as db:
            version = required(db.one("""SELECT v.*,d.corpus_type,d.company_id,d.report_year,c.stock_code,c.stock_name
            FROM t_document_version v JOIN t_document d ON d.id=v.document_id
            LEFT JOIN t_company c ON c.id=d.company_id WHERE v.id=?""", (version_id,)))
            db.execute("INSERT INTO t_parse_run(id,version_id,status,config_json,parser_identity,warnings_json,created_at) VALUES(?,?,?,?,?,?,?)",
                       (parse_id, version_id, "running", dump(config), "pending", "[]", now()))
        try:
            data = self.storage.read(version["storage_key"], version["file_sha256"])
            frames, identity, warnings = parse_file(data, version["source_format"], config)
            if version["corpus_type"] == "annual_report":
                identity["annual_identity"] = check_annual_identity(identity["opening"], version["stock_code"], version["stock_name"], version["report_year"])
            identity.pop("opening", None)
            count = 0
            with self.db.session() as db:
                for ordinal, frame in enumerate(frames):
                    frame_id = uid()
                    db.execute("INSERT INTO t_text_frame(id,parse_id,ordinal,physical_page,text,text_sha256,text_origin) VALUES(?,?,?,?,?,?,?)",
                               (frame_id, parse_id, ordinal, frame["physical_page"], frame["text"], frame["text_sha256"], frame["text_origin"]))
                    for start, end, piece in frame["spans"]:
                        if frame["text"][start:end] != piece:
                            raise DomainError("LOCATOR_MISMATCH", "原文区间校验失败", 409)
                        db.execute("INSERT INTO t_document_chunk(id,parse_id,frame_id,ordinal,char_start,char_end,content,content_sha256) VALUES(?,?,?,?,?,?,?,?)",
                                   (uid(), parse_id, frame_id, count, start, end, piece, sha(piece.encode())))
                        count += 1
                status = "partial" if any("requires_ocr" in w for w in warnings) else "done"
                db.execute("UPDATE t_parse_run SET status=?,parser_identity=?,warnings_json=? WHERE id=?",
                           (status, dump(identity), dump(warnings), parse_id))
            return {"parse_id": parse_id, "status": status, "frame_count": len(frames), "chunk_count": count,
                    "warnings": warnings, "parser": identity}
        except Exception as error:
            with self.db.session() as db:
                db.execute("UPDATE t_parse_run SET status='failed',error_code=? WHERE id=?",
                           (error.code if isinstance(error, DomainError) else "PARSE_FAILED", parse_id))
            raise

    def chunks(self, version_id, parse_id=None, page=1, size=20):
        with self.db.session() as db:
            if not parse_id:
                row = required(db.one("SELECT id FROM t_parse_run WHERE version_id=? AND status IN ('done','partial') ORDER BY created_at DESC,id LIMIT 1", (version_id,)))
                parse_id = row["id"]
            else:
                required(db.one("SELECT id FROM t_parse_run WHERE id=? AND version_id=?", (parse_id,version_id)))
            total = db.one("SELECT COUNT(*) AS n FROM t_document_chunk WHERE parse_id=?", (parse_id,))["n"]
            items = db.rows("""SELECT c.*,f.physical_page,f.text_origin FROM t_document_chunk c
                JOIN t_text_frame f ON f.id=c.frame_id WHERE c.parse_id=? ORDER BY c.ordinal LIMIT ? OFFSET ?""",
                (parse_id,size,(page-1)*size))
            return {"items":items,"total":total,"parse_id":parse_id,"page":page,"page_size":size}

    def source(self, chunk_id):
        with self.db.session() as db:
            row = required(db.one("""SELECT c.*,f.text AS frame_text,f.text_sha256 AS frame_sha256,f.physical_page,f.text_origin,
                v.id AS version_id,v.file_sha256,v.storage_key,v.source_format,v.metadata_json,p.config_json AS parse_config_json,p.warnings_json AS parse_warnings_json,d.title,d.corpus_type,d.report_year
                FROM t_document_chunk c JOIN t_text_frame f ON f.id=c.frame_id
                JOIN t_parse_run p ON p.id=c.parse_id JOIN t_document_version v ON v.id=p.version_id
                JOIN t_document d ON d.id=v.document_id WHERE c.id=?""",(chunk_id,)))
        source_available = True
        try:
            self.storage.read(row["storage_key"], row["file_sha256"])
        except DomainError as error:
            if error.code != "SOURCE_UNAVAILABLE":
                raise
            source_available = False
        if sha(row["frame_text"].encode()) != row["frame_sha256"] or row["frame_text"][row["char_start"]:row["char_end"]] != row["content"]:
            raise DomainError("LOCATOR_MISMATCH", "文本帧或原文位置校验失败", 409)
        row["metadata"] = json.loads(row.pop("metadata_json"))
        row.pop("storage_key")
        row["source_available"] = source_available
        row["locator_status"] = "frame_hash_verified"
        return row

    def file(self, version_id):
        with self.db.session() as db:
            row = required(db.one("SELECT * FROM t_document_version WHERE id=?", (version_id,)))
        self.storage.read(row["storage_key"],row["file_sha256"])
        return self.storage.path(row["storage_key"]), row["filename"]

    def claim_job(self, worker_id):
        moment = now()
        lease = (datetime.now(timezone.utc)+timedelta(seconds=600)).isoformat()
        with self.db.session() as db:
            db.execute("UPDATE t_job SET status='failed',error_code='WORKER_ATTEMPTS_EXHAUSTED',updated_at=? WHERE status='running' AND lease_until<? AND attempt_count>=3",(moment,moment))
            row = db.one("""SELECT * FROM t_job WHERE status='queued' OR
             (status='running' AND lease_until<? AND attempt_count<3) ORDER BY created_at,id LIMIT 1""",(moment,))
            if not row:
                return None
            updated = db.execute("""UPDATE t_job SET status='running',lease_owner=?,lease_until=?,attempt_count=attempt_count+1,updated_at=?
              WHERE id=? AND (status='queued' OR (status='running' AND lease_until<?))""",
              (worker_id,lease,moment,row["id"],moment)).rowcount
            return row if updated else None

    def heartbeat(self, job_id, worker_id):
        with self.db.session() as db:
            count = db.execute("UPDATE t_job SET lease_until=?,updated_at=? WHERE id=? AND status='running' AND lease_owner=?",
                ((datetime.now(timezone.utc)+timedelta(seconds=600)).isoformat(),now(),job_id,worker_id)).rowcount
            if count != 1:
                raise DomainError("LEASE_LOST", "任务租约已丢失",409)

    def finish(self, job_id, worker_id, result=None, error=None):
        with self.db.session() as db:
            count = db.execute("UPDATE t_job SET status=?,result_json=?,error_code=?,lease_until=NULL,updated_at=? WHERE id=? AND lease_owner=? AND status='running'",
                ("failed" if error else "succeeded",dump(result) if result else None,error,now(),job_id,worker_id)).rowcount
            if count != 1:
                raise DomainError("LEASE_LOST", "结果提交时任务租约已丢失",409)
