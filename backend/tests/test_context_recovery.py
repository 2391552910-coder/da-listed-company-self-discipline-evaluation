import json
import pytest
from app.documents.common import DomainError,dump
from app.documents.jobs import JobControl
from app.documents.worker import work_once
from app.documents.indexes import Indexes
from test_context_service import ready,submit

def test_recovery_reuses_an_immutable_published_snapshot(ready,monkeypatch):
    task=submit(ready)
    real_finish=JobControl.finish
    crashed={"once":False}
    def crash_before_finish(control,job_id,worker_id,result=None,error=None,status=None):
        if result and result.get("snapshot_sha256") and not crashed["once"]:
            crashed["once"]=True
            with ready.docs.db.session() as db:
                db.execute("UPDATE t_job SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",(job_id,))
            raise DomainError("LEASE_LOST","Injected crash after durable snapshot",409)
        return real_finish(control,job_id,worker_id,result,error,status)
    monkeypatch.setattr(JobControl,"finish",crash_before_finish)
    first=work_once(ready.docs)
    assert first["status"]=="running"
    assert ready.contexts.get(task["run_id"])["snapshot"] is None
    with ready.docs.db.session() as db:
        original=db.one("SELECT snapshot_sha256 FROM t_retrieval_run WHERE id=?",(task["run_id"],))["snapshot_sha256"]
    monkeypatch.setattr(Indexes,"search",lambda *a,**kw:pytest.fail("Recovery must not retrieve again"))
    recovered=work_once(ready.docs)
    assert recovered["status"]=="succeeded"
    assert recovered["attempt_count"]==2
    assert recovered["result"]["recovered_snapshot"]
    assert recovered["result"]["snapshot_origin_attempt"]==1
    replay=ready.contexts.get(task["run_id"])
    assert replay["snapshot_sha256"]==original
    assert replay["snapshot"]["attempt_number"]==1
    with ready.docs.db.session() as db:
        attempts=db.rows("SELECT status FROM t_job_attempt WHERE job_id=? ORDER BY attempt_number",(task["id"],))
    assert [a["status"] for a in attempts]==["abandoned","succeeded"]

@pytest.mark.parametrize("change",["missing_sha","wrong_run","wrong_attempt"])
def test_committed_snapshot_requires_matching_job_proof(ready,change):
    task=submit(ready)
    done=work_once(ready.docs)
    proof=done["result"]
    if change=="missing_sha":proof.pop("snapshot_sha256")
    if change=="wrong_run":proof["run_id"]="other-run"
    if change=="wrong_attempt":proof["snapshot_origin_attempt"]=999
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_job SET result_json=? WHERE id=?",(dump(proof),task["id"]))
    with pytest.raises(DomainError) as caught:
        ready.contexts.get(task["run_id"])
    assert caught.value.code=="CONTEXT_JOB_MISMATCH"

def test_cancel_during_retrieval_never_exposes_context(ready,monkeypatch):
    task=submit(ready)
    original=Indexes.search
    def cancel_after_search(indexes,*args,**kwargs):
        result=original(indexes,*args,**kwargs)
        JobControl(ready.docs).cancel(task["id"])
        return result
    monkeypatch.setattr(Indexes,"search",cancel_after_search)
    done=work_once(ready.docs)
    assert done["status"]=="cancelled" and done["result"] is None
    assert ready.contexts.get(task["run_id"])["snapshot"] is None
