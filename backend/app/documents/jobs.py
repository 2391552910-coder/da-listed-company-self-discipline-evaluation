"""Persistent task cancellation, retries and fenced worker completion.

No model/API work occurs while a database transaction is open. Retries create a
new job and, for index/context jobs, a new immutable business execution identity.
"""
from datetime import datetime, timedelta, timezone
import json

from app.documents.common import DomainError, dump, now, sha, uid

TERMINAL = {"succeeded", "partial", "failed", "cancelled", "budget_exhausted"}
RETRYABLE = {"failed", "cancelled", "budget_exhausted"}
STATUSES = TERMINAL | {"queued", "running"}


def _required(row):
    if row is None:
        raise DomainError("NOT_FOUND", "记录不存在", 404)
    return row


class JobControl:
    def __init__(self, documents):
        self.docs = documents

    def _public(self, row):
        result = self.docs.public_job(row)
        result.update(
            cancel_requested=bool(row.get("cancel_requested", 0)),
            parent_job_id=row.get("parent_job_id"),
            progress=json.loads(row.get("progress_json") or "{}"),
        )
        return result

    def list(self, page=1, size=20, status=None):
        if (isinstance(page, bool) or not isinstance(page, int) or page < 1 or
                isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 200):
            raise DomainError("INVALID_PAGE", "分页参数不合法", 422)
        if status is not None and status not in STATUSES:
            raise DomainError("INVALID_JOB_STATUS", "任务状态不合法", 422)
        where = " WHERE status=?" if status is not None else ""
        args = (status,) if status is not None else ()
        with self.docs.db.session() as db:
            total = db.one("SELECT COUNT(*) AS n FROM t_job" + where, args)["n"]
            rows = db.rows("SELECT * FROM t_job" + where +
                           " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                           (*args, size, (page - 1) * size))
        return {"items": [self._public(row) for row in rows], "total": total,
                "page": page, "page_size": size}

    @staticmethod
    def _sync_run(db,row,status):
        result=json.loads(row["result_json"]) if row.get("result_json") else {}
        if row["kind"]=="context":
            if status in {"cancelled","failed"} or (status=="budget_exhausted" and not result.get("snapshot_sha256")):
                db.execute("UPDATE t_retrieval_run SET status=?,snapshot_key=NULL,snapshot_sha256=NULL WHERE job_id=?",(status,row["id"]))
            else:
                db.execute("UPDATE t_retrieval_run SET status=? WHERE job_id=?",(status,row["id"]))
        elif row["kind"]=="index" and status in {"cancelled","failed","budget_exhausted"}:
            payload=json.loads(row["payload_json"])
            if isinstance(payload,dict) and payload.get("index_id"):
                db.execute("UPDATE t_index_version SET status='failed' WHERE id=? AND status!='ready'",(payload["index_id"],))

    def cancel(self, job_id):
        moment = now()
        with self.docs.db.session() as db:
            row = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
            if row["status"] in TERMINAL:
                return self._public(row)
            # Conditional updates handle a worker claiming queued work concurrently.
            db.execute("""UPDATE t_job SET status='cancelled',cancel_requested=1,
                error_code='TASK_CANCELLED',lease_until=NULL,updated_at=?
                WHERE id=? AND status='queued'""", (moment, job_id))
            db.execute("""UPDATE t_job SET cancel_requested=1,updated_at=?
                WHERE id=? AND status='running'""", (moment, job_id))
            row = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
            if row["status"] == "cancelled":
                self._sync_run(db, row, "cancelled")
            return self._public(row)

    def checkpoint(self, job_id, worker_id):
        moment = now()
        lease = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        with self.docs.db.session() as db:
            row = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
            self._validate_owner(row, worker_id, moment)
            if row.get("cancel_requested"):
                raise DomainError("TASK_CANCELLED", "任务已请求取消", 409)
            count = db.execute("""UPDATE t_job SET lease_until=?,updated_at=?
                WHERE id=? AND lease_owner=? AND status='running'
                AND lease_until>=? AND cancel_requested=0""",
                (lease, moment, job_id, worker_id, moment)).rowcount
            if count != 1:
                current = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
                self._validate_owner(current, worker_id, moment)
                if current.get("cancel_requested"):
                    raise DomainError("TASK_CANCELLED", "任务已请求取消", 409)
                raise DomainError("LEASE_LOST", "任务租约已丢失", 409)
        return {"job_id": job_id, "lease_until": lease}

    @staticmethod
    def _validate_owner(row, worker_id, moment):
        if (row["status"] != "running" or row.get("lease_owner") != worker_id or
                not row.get("lease_until") or row["lease_until"] < moment):
            raise DomainError("LEASE_LOST", "任务租约已丢失或过期", 409)

    def finish(self, job_id, worker_id, result=None, error=None, status=None):
        error_code = error.code if isinstance(error, DomainError) else error
        if error_code is not None and not isinstance(error_code, str):
            raise DomainError("INVALID_ERROR_CODE", "任务错误码必须是字符串", 422)
        if status is not None and status not in TERMINAL:
            raise DomainError("INVALID_JOB_STATUS", "只能提交任务终态", 422)
        moment = now()
        with self.docs.db.session() as db:
            row = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
            self._validate_owner(row, worker_id, moment)
            if row.get("cancel_requested") or error_code == "TASK_CANCELLED":
                outcome, error_code = "cancelled", "TASK_CANCELLED"
            elif error_code == "BUDGET_EXHAUSTED":
                outcome = "budget_exhausted"
            elif error_code:
                outcome = "failed"
            else:
                outcome = status or "succeeded"
            # Re-check the cancellation flag atomically at the write boundary.
            count = db.execute("""UPDATE t_job SET
                status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE ? END,
                result_json=CASE WHEN cancel_requested=1 OR ?='cancelled' THEN NULL ELSE ? END,
                error_code=CASE WHEN cancel_requested=1 THEN 'TASK_CANCELLED' ELSE ? END,
                lease_until=NULL,updated_at=?
                WHERE id=? AND lease_owner=? AND status='running' AND lease_until>=?""",
                (outcome, outcome, dump(result) if result is not None else None, error_code,
                 moment, job_id, worker_id, moment)).rowcount
            if count != 1:
                raise DomainError("LEASE_LOST", "结果提交时任务租约已丢失", 409)
            final = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
            attempt_count = db.execute("""UPDATE t_job_attempt SET finished_at=?,status=?,error_code=?,result_json=?
                WHERE job_id=? AND worker_id=? AND attempt_number=? AND status='running'""",
                (moment, final["status"], final["error_code"], final.get("result_json"),
                 job_id, worker_id, row["attempt_count"])).rowcount
            if attempt_count != 1:
                raise DomainError("ATTEMPT_MISMATCH", "任务缺少当前执行尝试记录", 409)
            self._sync_run(db, final, final["status"])
            return self._public(final)

    def _prepare_retry(self, db, row):
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            raise DomainError("INVALID_JOB_PAYLOAD", "历史任务快照不可解析", 409)
        if not isinstance(payload, dict):
            raise DomainError("INVALID_JOB_PAYLOAD", "历史任务快照必须为对象", 409)
        if row["kind"] == "parse":
            return payload
        if row["kind"] == "index":
            previous = _required(db.one("SELECT * FROM t_index_version WHERE id=?",
                                       (payload.get("index_id"),)))
            index_id = uid()
            db.execute("""INSERT INTO t_index_version(id,status,profile,config_json,created_at)
                VALUES(?,?,?,?,?)""", (index_id, "staging", previous["profile"],
                                      previous["config_json"], now()))
            return dict(payload, index_id=index_id)
        callback = getattr(self.docs, "retry_payload", None)
        if callable(callback):
            prepared = callback(row["kind"], payload, db)
            if not isinstance(prepared, dict):
                raise DomainError("INVALID_RETRY_PAYLOAD", "重试快照必须为对象", 409)
            if row["kind"] == "context" and prepared.get("run_id") == payload.get("run_id"):
                raise DomainError("RETRY_IDENTITY_REUSED", "重试必须新建检索运行", 409)
            return prepared
        raise DomainError("RETRY_PAYLOAD_UNAVAILABLE", "该任务类型缺少重试身份适配", 409)

    def retry(self, job_id, key=None):
        if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > 200):
            raise DomainError("INVALID_IDEMPOTENCY_KEY", "幂等键为空或过长", 422)
        try:
            with self.docs.db.session() as db:
                previous = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
                request = {"retry_of": job_id, "kind": previous["kind"],
                           "payload_sha256": sha(previous["payload_json"].encode())}
                digest = sha(dump(request).encode())
                if key:
                    old = db.one("SELECT * FROM t_job WHERE idempotency_key=?", (key,))
                    if old:
                        if old["request_sha256"] != digest or old["kind"] != previous["kind"]:
                            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应其他重试请求", 409)
                        return self._public(old)
                if previous["status"] not in RETRYABLE:
                    raise DomainError("JOB_NOT_RETRYABLE", "仅失败、取消或预算耗尽任务可重试", 409)
                payload = self._prepare_retry(db, previous)
                result = self.docs._enqueue(db, previous["kind"], payload, key, request)
                db.execute("UPDATE t_job SET parent_job_id=? WHERE id=?", (job_id, result["id"]))
                if previous["kind"] == "context":
                    if not payload.get("run_id"):
                        raise DomainError("INVALID_RETRY_PAYLOAD", "重试缺少新检索运行", 409)
                    bound = db.execute("""UPDATE t_retrieval_run SET job_id=?
                        WHERE id=? AND job_id IS NULL""", (result["id"], payload["run_id"])).rowcount
                    if bound != 1:
                        raise DomainError("RETRY_RUN_CONFLICT", "新检索运行已绑定其他任务或不存在", 409)
                return self._public(_required(db.one("SELECT * FROM t_job WHERE id=?", (result["id"],))))
        except DomainError:
            raise
        except Exception as error:
            # A concurrent identical key may win the UNIQUE constraint. Its resource
            # creation rolls back with this transaction; return only the matching job.
            sqlstate = getattr(error, "sqlstate", None)
            if sqlstate is None:
                sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
            unique = sqlstate == "23505" or (
                error.__class__.__name__ == "IntegrityError" and "UNIQUE" in str(error).upper())
            if key and unique:
                with self.docs.db.session() as db:
                    old = db.one("SELECT * FROM t_job WHERE idempotency_key=?", (key,))
                    previous = _required(db.one("SELECT * FROM t_job WHERE id=?", (job_id,)))
                    request = {"retry_of": job_id, "kind": previous["kind"],
                               "payload_sha256": sha(previous["payload_json"].encode())}
                    if old and old["request_sha256"] == sha(dump(request).encode()) and old["kind"] == previous["kind"]:
                        return self._public(old)
                    if old:
                        raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键对应其他重试请求", 409)
            raise
