import json
import time
from app.documents.common import DomainError,dump,now,uid
from app.documents.indexes import Indexes
from app.documents.jobs import JobControl

def work_once(documents):
    worker_id=uid()
    job=documents.claim_job(worker_id)
    if not job:
        return None
    jobs=JobControl(documents)
    heartbeat=lambda:jobs.checkpoint(job["id"],worker_id)
    try:
        heartbeat()
        payload=json.loads(job["payload_json"])
        if job["kind"]=="parse":
            result=documents.parse(payload["version_id"],payload["config"],heartbeat,job["id"],worker_id)
        elif job["kind"]=="index":
            result=Indexes(documents).build(payload,heartbeat,job["id"],worker_id)
        elif job["kind"]=="context":
            from app.retrieval.service import Contexts
            result=Contexts(documents).build(payload,job["id"],worker_id,heartbeat)
        elif job["kind"]=="evaluation":
            from app.evaluation.service import Evaluations
            result=Evaluations(documents).build(payload,job["id"],worker_id,heartbeat)
        else:
            raise DomainError("UNKNOWN_JOB_KIND","未知任务类型")
        heartbeat()
        outcome=result.get("status")
        jobs.finish(job["id"],worker_id,result=result,
                    status=outcome if outcome in {"failed","budget_exhausted","partial"} else None,
                    error=("BUDGET_EXHAUSTED" if outcome=="budget_exhausted" else
                           result.get("error_code") if outcome!="partial" else None))
    except Exception as error:
        if isinstance(error,DomainError) and error.code=="LEASE_LOST":
            return documents.job(job["id"])
        try:
            jobs.finish(job["id"],worker_id,error=error.code if isinstance(error,DomainError) else "TASK_FAILED")
        except DomainError as failure:
            if failure.code!="LEASE_LOST":
                raise
    return documents.job(job["id"])

def run(documents,once=False):
    while True:
        result=work_once(documents)
        if once:
            return result
        if result is None:
            time.sleep(1)
