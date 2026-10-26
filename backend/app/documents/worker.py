import json
import time
import signal
import threading
from app.documents.common import DomainError,dump,now,uid
from app.documents.indexes import Indexes
from app.documents.jobs import JobControl

def work_once(documents,worker_id=None,health_callback=None):
    worker_id=worker_id or uid()
    job=documents.claim_job(worker_id)
    if not job:
        return None
    jobs=JobControl(documents)
    def heartbeat():
        jobs.checkpoint(job["id"],worker_id)
        if health_callback:
            health_callback("busy",job["id"])
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

def run(documents,once=False,stop_event=None):
    from app.documents.worker_health import record_worker
    stopped=stop_event or threading.Event()
    worker_id=uid()
    last=[None,0.0]
    handlers={}
    def health(status,job_id=None):
        state=(status,job_id)
        if state!=last[0] or time.monotonic()-last[1]>=10:
            record_worker(documents,worker_id,status,job_id)
            last[:]=[state,time.monotonic()]
    if threading.current_thread() is threading.main_thread():
        for kind in (signal.SIGTERM,signal.SIGINT):
            handlers[kind]=signal.getsignal(kind)
            signal.signal(kind,lambda *_:stopped.set())
    try:
        while not stopped.is_set():
            health("idle")
            result=work_once(documents,worker_id,health)
            if once:
                return result
            if result is None:
                stopped.wait(1)
    finally:
        record_worker(documents,worker_id,"stopped")
        for kind,handler in handlers.items():
            signal.signal(kind,handler)
