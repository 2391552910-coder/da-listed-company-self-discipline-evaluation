"""Independent stage6/7 integration checks with explicitly synthetic model output."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

from app.documents.common import dump, uid
from app.documents.jobs import JobControl
from app.documents.worker import work_once
from app.evaluation.service import Evaluations
from app.experiments.service import Experiments
from test_context_service import ready
from test_evaluation_service import MockProvider


class AgentFixtureProvider(MockProvider):
    def complete(self, messages, **kwargs):
        system=messages[0]["content"]
        if system.startswith("[DALUN_AGENT_PLAN_V1]"):
            text={"action":"stop","query":None,"source_role":None,"reason":"independent synthetic fixture"}
        elif system.startswith("[DALUN_AGENT_VERIFY_V1]"):
            text={"decision":"accept","reason":"fixture review is not human support","issues":[]}
        else:
            return super().complete(messages,**kwargs)
        self.calls.append({"messages":messages})
        kwargs["heartbeat"]()
        return {"text":dump(text),"usage":self.usage,"finish_reason":"stop",
                "model":self.model,"request_id":"independent-fixture-"+str(len(self.calls)),"mock":True}


def request(ready,modes=None):
    return {"name":"Independent synthetic acceptance",
        "cases":[{"key":"case-1","index_id":ready.index_id,"stock_code":"000001","report_year":2024}],
        "modes":modes or ["dual_fixed"],
        "questions":[{"key":"audit-1","dimension":"D2","text":"审计委员会履职"}],
        "retrieval":{"tokenizer_kind":"utf8_diagnostic"},
        "budget":{"max_model_calls":4},"max_total_model_calls":8}


def test_cancel_reconciles_child_queued_before_item_binding_crash(ready,monkeypatch):
    service=Evaluations(ready.docs,AgentFixtureProvider())
    experiments=Experiments(ready.docs,service)
    actual=service.get
    def crash(*args):
        raise SystemExit("simulated API crash after durable child queue, before experiment binding")
    monkeypatch.setattr(service,"get",crash)
    with pytest.raises(SystemExit):experiments.create(**request(ready),key="orphan-cancel")
    monkeypatch.setattr(service,"get",actual)
    with ready.docs.db.session() as db:
        parent=db.one("SELECT id FROM t_experiment")
        job=db.one("SELECT * FROM t_job WHERE kind='evaluation'")
        assert job["status"]=="queued"
        assert db.one("SELECT run_id FROM t_experiment_item")["run_id"] is None
    cancelled=experiments.cancel(parent["id"])
    assert ready.docs.job(job["id"])["status"]=="cancelled"
    assert cancelled["cancel_requested"] is True
    replay=experiments.dispatch(parent["id"])
    assert replay["status"]=="cancelled"
    with ready.docs.db.session() as db:
        assert db.one("SELECT COUNT(*) AS n FROM t_job WHERE kind='evaluation'")["n"]==1


def test_cancelled_parent_dispatch_reconciles_already_bound_active_job(ready):
    service=Evaluations(ready.docs,AgentFixtureProvider())
    experiments=Experiments(ready.docs,service)
    created=experiments.create(**request(ready))
    item=created["items"][0]
    assert ready.docs.job(item["job_id"])["status"]=="queued"
    # This is the persisted state after an API process exits between setting the
    # parent flag and issuing the child cancellation request.
    with ready.docs.db.session() as db:
        db.execute("UPDATE t_experiment SET cancel_requested=1 WHERE id=?",(created["id"],))
    resumed=experiments.dispatch(created["id"])
    assert ready.docs.job(item["job_id"])["status"]=="cancelled"
    assert resumed["status"]=="cancelled"


def test_http_agent_experiment_rating_history_export_and_health(ready,monkeypatch):
    pytest.importorskip("fastapi",reason="HTTP dependencies unavailable locally; CI executes this path")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient
    from app.documents.api import create_app
    from app.documents.worker_health import record_worker
    provider=AgentFixtureProvider()
    monkeypatch.setattr("app.evaluation.provider.Provider",lambda settings:provider)
    app=create_app(ready.docs.settings)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code==200
        assert client.get("/health/workers").status_code==503
        record_worker(ready.docs,uid(),"idle")
        assert client.get("/health/workers").json()["live_count"]==1
        payload=request(ready,["dual_fixed","dual_agent"])
        response=client.post("/api/v1/experiments",json=payload,headers={"Idempotency-Key":"http-experiment"})
        assert response.status_code==202,response.text
        experiment=response.json()["data"]
        same=client.post("/api/v1/experiments",json=payload,headers={"Idempotency-Key":"http-experiment"})
        assert same.json()["data"]["id"]==experiment["id"]
        for _ in range(2):assert work_once(ready.docs)["status"]=="succeeded"
        result=client.get("/api/v1/experiments/"+experiment["id"]).json()["data"]
        assert result["status"]=="succeeded"
        assert result["comparison"]["groups"][0]["comparable"] is True
        assert all(run["quality_score"] is None for run in result["comparison"]["runs"])
        agent=next(item for item in result["items"] if item["mode"]=="dual_agent")
        run=client.get("/api/v1/evaluations/"+agent["run_id"]).json()["data"]
        answer=run["answers"][0]
        assert [call["purpose"] for call in run["model_calls"]]==["agent_plan","agent_draft","agent_verify"]
        assert answer["agent_trace"]["human_review_status"]=="not_reviewed"
        rating={"run_id":run["id"],"answer_id":answer["id"],"source_kind":"manual",
            "rubric_version":"evidence-quality-v1","reviewer":"synthetic tester","method_version":"fixture-v1",
            "values":{"faithfulness":3,"coverage":None,"professionalism":4,"citation_quality":2}}
        rated=client.post(f"/api/v1/experiments/{experiment['id']}/ratings",json=rating,headers={"Idempotency-Key":"http-rating"})
        assert rated.status_code==201,rated.text
        repeated=client.post(f"/api/v1/experiments/{experiment['id']}/ratings",json=rating,headers={"Idempotency-Key":"http-rating"})
        assert repeated.json()["data"]["id"]==rated.json()["data"]["id"]
        history={"label":"synthetic historical fixture","source_identity":"fixture-source-v1",
            "records":[{"record_key":"old-1","mode":"unknown","notes":"Not a current evaluation run"}]}
        archived=client.post(f"/api/v1/experiments/{experiment['id']}/history",json=history)
        assert archived.status_code==201,archived.text
        exported=client.get(f"/api/v1/experiments/{experiment['id']}/exports",params={"format":"json"})
        assert exported.status_code==200
        frozen=json.loads(exported.content)
        assert frozen["historical_records"][0]["origin"]=="historical_import"
        assert len(frozen["items"])==2
        assert frozen["ratings"][0]["answer_snapshot_sha256"]
        csv=client.get(f"/api/v1/experiments/{experiment['id']}/exports",params={"format":"csv"})
        assert csv.status_code==200 and csv.content.startswith(b"\xef\xbb\xbf")
        assert "manual" in csv.text and "historical_import" in csv.text


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"),reason="Disposable PostgreSQL is available in CI only")
def test_postgres_agent_steps_experiment_binding_and_rating_provenance(tmp_path,monkeypatch):
    from app.documents.indexes import Indexes
    from app.documents.service import Documents
    from app.documents.settings import Settings
    from test_documents import annual_fixture,parsed_law
    docs=Documents(Settings(os.environ["TEST_POSTGRES_URL"],tmp_path/"stage67-pg",sys.executable))
    docs.db.migrate()
    _,law=parsed_law(docs)
    _,annual=annual_fixture(docs,monkeypatch,stock="019996",year=2024)
    queued=Indexes(docs).enqueue([law["parse_id"],annual["parse_id"]],"bm25_diagnostic",False)
    indexed=work_once(docs)
    assert indexed["id"]==queued["id"] and indexed["status"]=="succeeded"
    provider=AgentFixtureProvider()
    monkeypatch.setattr("app.evaluation.provider.Provider",lambda settings:provider)
    service=Evaluations(docs,provider)
    experiments=Experiments(docs,service)
    config=request(SimpleNamespace(index_id=indexed["result"]["index_id"]),["dual_fixed","dual_agent"])
    config["cases"][0]["stock_code"]="019996"
    created=experiments.create(**config)
    for _ in range(2):assert work_once(docs)["status"]=="succeeded"
    completed=experiments.get(created["id"])
    assert completed["status"]=="succeeded"
    assert completed["comparison"]["groups"][0]["comparable"]
    agent=next(item for item in completed["items"] if item["mode"]=="dual_agent")
    run=service.get(agent["run_id"])
    assert [call["step_key"] for call in run["model_calls"]]==["plan:1","draft","verify"]
    answer=run["answers"][0]
    rating=experiments.add_rating(created["id"],run["id"],answer["id"],"manual",
        "evidence-quality-v1",{"faithfulness":3,"coverage":None,"professionalism":3,"citation_quality":4},
        "PostgreSQL synthetic reviewer","fixture-v1")
    with docs.db.session() as db:
        stored=db.one("SELECT snapshot_sha256 FROM t_evaluation_answer WHERE id=?",(answer["id"],))
        assert rating["answer_snapshot_sha256"]==stored["snapshot_sha256"]
        if os.getenv("TEST_LEGACY_SEED"):
            assert db.one("SELECT id FROM t_company WHERE stock_code='019998'")
