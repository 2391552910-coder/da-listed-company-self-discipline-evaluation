from datetime import datetime, timedelta, timezone
import signal
import sys
import threading

import pytest

from app.documents.common import DomainError, uid
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import run
from app.documents.worker_health import record_worker, worker_health


@pytest.fixture
def docs(tmp_path):
    docs=Documents(Settings("sqlite:///"+str(tmp_path/"health.sqlite"),tmp_path/"assets",sys.executable))
    docs.db.migrate()
    return docs


def test_health_excludes_stale_and_stopped_workers_and_private_identity(docs):
    assert worker_health(docs)["healthy"] is False
    first,second=uid(),uid()
    record_worker(docs,first,"idle")
    record_worker(docs,second,"busy")
    result=worker_health(docs)
    assert result["live_count"]==2 and result["idle_count"]==result["busy_count"]==1
    assert first not in str(result) and "hostname" not in result
    record_worker(docs,first,"stopped")
    stale=(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat()
    with docs.db.session() as db:
        db.execute("UPDATE t_worker_health SET heartbeat_at=? WHERE worker_id=?",(stale,second))
    assert worker_health(docs)["healthy"] is False
    assert worker_health(docs)["registered_count"]==2


@pytest.mark.parametrize("age",[0,-1,True,1.5,3601])
def test_health_window_validation(docs,age):
    with pytest.raises(DomainError):worker_health(docs,age)


def test_once_processes_real_job_and_restores_signal_handlers(docs):
    before={kind:signal.getsignal(kind) for kind in (signal.SIGTERM,signal.SIGINT)}
    version=docs.upload("合成规则仅供健康检查测试".encode(),"fixture.txt","regulation","fixture")
    queued=docs.enqueue_parse(version["id"])
    result=run(docs,once=True)
    assert result["id"]==queued["id"] and result["status"]=="succeeded"
    assert worker_health(docs)["live_count"]==0
    with docs.db.session() as db:
        row=db.one("SELECT * FROM t_worker_health")
        assert row["status"]=="stopped" and row["job_id"] is None
    assert {kind:signal.getsignal(kind) for kind in before}==before


def test_idle_thread_stops_cooperatively_and_records_stopped(docs,monkeypatch):
    observed=threading.Event()
    stopped=threading.Event()
    def idle(*args):
        observed.set()
        return None
    monkeypatch.setattr("app.documents.worker.work_once",idle)
    thread=threading.Thread(target=run,args=(docs,),kwargs={"stop_event":stopped})
    thread.start()
    try:
        assert observed.wait(3)
        assert worker_health(docs)["healthy"]
    finally:
        stopped.set()
        thread.join(3)
    assert not thread.is_alive() and not worker_health(docs)["healthy"]
