import json
import time
from app.documents.common import DomainError, uid
from app.documents.indexes import Indexes

def work_once(documents):
    worker_id=uid()
    job=documents.claim_job(worker_id)
    if not job:
        return None
    heartbeat=lambda:documents.heartbeat(job["id"],worker_id)
    try:
        payload=json.loads(job["payload_json"])
        if job["kind"]=="parse":
            result=documents.parse(payload["version_id"],payload["config"])
        elif job["kind"]=="index":
            result=Indexes(documents).build(payload,heartbeat)
        else:
            raise DomainError("UNKNOWN_JOB_KIND","未知任务类型")
        documents.finish(job["id"],worker_id,result=result)
    except Exception as error:
        if job["kind"]=="index":
            with documents.db.session() as db:
                db.execute("UPDATE t_index_version SET status='failed' WHERE id=? AND status!='ready'",
                           (json.loads(job["payload_json"])["index_id"],))
        documents.finish(job["id"],worker_id,error=error.code if isinstance(error,DomainError) else "TASK_FAILED")
    return documents.job(job["id"])

def run(documents,once=False):
    while True:
        result=work_once(documents)
        if once:
            return result
        if result is None:
            time.sleep(1)
