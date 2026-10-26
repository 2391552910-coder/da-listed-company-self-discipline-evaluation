import sys
import time
from types import SimpleNamespace
import pytest
from app.documents.common import DomainError
from app.documents.indexes import Indexes
from app.documents.settings import Settings

class RunningProcess:
    def __init__(self):
        self.returncode=None
        self.terminated=False
    def poll(self):return self.returncode
    def terminate(self):self.terminated=True;self.returncode=-1
    def wait(self,timeout=None):return self.returncode
    def kill(self):self.returncode=-9

@pytest.mark.parametrize("reason",["deadline","cancel"])
def test_active_model_process_is_stopped_at_control_boundary(tmp_path,monkeypatch,reason):
    process=RunningProcess()
    monkeypatch.setattr("app.documents.indexes.subprocess.Popen",lambda *a,**kw:process)
    docs=SimpleNamespace(settings=Settings("sqlite:///"+str(tmp_path/"d"),tmp_path,sys.executable))
    def cancelled():
        raise DomainError("TASK_CANCELLED","cancelled")
    with pytest.raises(DomainError) as caught:
        Indexes(docs)._invoke("query",tmp_path,"bm25_diagnostic",
            heartbeat=cancelled if reason=="cancel" else None,
            deadline=time.monotonic()+.1 if reason=="deadline" else None)
    assert process.terminated
    assert caught.value.code==("BUDGET_EXHAUSTED" if reason=="deadline" else "TASK_CANCELLED")

def test_expired_deadline_never_starts_model_process(tmp_path,monkeypatch):
    monkeypatch.setattr("app.documents.indexes.subprocess.Popen",lambda *a,**kw:pytest.fail("Expired budget must not start a process"))
    docs=SimpleNamespace(settings=Settings("sqlite:///"+str(tmp_path/"d"),tmp_path,sys.executable))
    with pytest.raises(DomainError) as caught:
        Indexes(docs)._invoke("query",tmp_path,"bm25_diagnostic",deadline=time.monotonic()-1)
    assert caught.value.code=="BUDGET_EXHAUSTED"
