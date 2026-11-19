from contextlib import contextmanager
from dataclasses import replace
import json
import socket
from threading import Thread
import time

from fastapi.testclient import TestClient
import pytest
import uvicorn

from app.documents.api import create_app
from app.documents.indexes import Indexes
from app.documents.worker import work_once
from app.measurement.api import create_app as create_measurement
from app.measurement.service import INDICATORS, Measurement
from test_context_service import ready


def inputs(root, *, dataset_version="fixtures-1"):
    dataset = {"version": "fixtures-1", "rows": [{"stock_code": "000001", "report_year": 2024,
        "indicators": {f"I{number:02d}": number * 5 for number in range(1, 13)}}]}
    weights = {"version": "fixture-weights-1", "dataset_version": dataset_version,
               "weights": dict.fromkeys(INDICATORS, 1 / 12)}
    (root / "indicators.json").write_text(json.dumps(dataset), encoding="utf-8")
    (root / "weights.json").write_text(json.dumps(weights), encoding="utf-8")


@contextmanager
def live_service(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Measurement server did not start")
                time.sleep(.01)
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(5)


def test_service_validates_both_files_then_computes_with_provenance(tmp_path):
    measurement = Measurement(tmp_path)
    assert measurement.status() == {"status": "inputs_missing", "missing": ["dataset", "weights"]}
    inputs(tmp_path)
    result = measurement.result("000001", 2024)
    assert result["status"] == "ready"
    assert result["result"]["score"] == pytest.approx(32.5)
    assert result["result"]["dimensions"] == pytest.approx({"D1": 10, "D2": 25, "D3": 40, "D4": 55})
    assert len(result["dataset_sha256"]) == len(result["weights_sha256"]) == 64
    assert measurement.result("000002", 2024)["result"] is None
    inputs(tmp_path, dataset_version="wrong")
    assert measurement.status() == {"status": "inputs_invalid", "issue": "dataset_version_mismatch"}
    (tmp_path / "weights.json").unlink()
    assert measurement.status() == {"status": "inputs_missing", "missing": ["weights"]}


def test_bad_weights_and_incomplete_indicator_rows_do_not_generate_scores(tmp_path):
    inputs(tmp_path)
    path = tmp_path / "weights.json"
    weights = json.loads(path.read_text(encoding="utf-8"))
    weights["weights"]["I01"] = 0
    path.write_text(json.dumps(weights), encoding="utf-8")
    measurement = Measurement(tmp_path)
    assert measurement.result("000001", 2024) == {"status": "inputs_invalid", "issue": "weights", "result": None}
    inputs(tmp_path)
    path = tmp_path / "indicators.json"
    dataset = json.loads(path.read_text(encoding="utf-8"))
    dataset["rows"][0]["indicators"].pop("I12")
    path.write_text(json.dumps(dataset), encoding="utf-8")
    assert measurement.result("000001", 2024) == {"status": "inputs_invalid", "issue": "dataset", "result": None}


def test_core_and_retrieval_report_with_disabled_missing_online_and_stopped_service(ready, tmp_path):
    # Exercise the actual HTTP bridge, not a patched successful response.
    with live_service(create_measurement(tmp_path)) as url:
        connected = TestClient(create_app(replace(ready.docs.settings, measurement_url=url)))
        assert connected.get("/api/v1/measurement/status").json()["data"]["status"] == "inputs_missing"
        assert connected.get("/health/ready").status_code == 200
        inputs(tmp_path)
        assert connected.get("/api/v1/measurement/status").json()["data"]["status"] == "ready"
        result = connected.get("/api/v1/measurement/results/000001.SZ?report_year=2024").json()["data"]
        assert result["result"]["score"] == pytest.approx(32.5)
    assert connected.get("/api/v1/measurement/results/000001?report_year=2024").json()["data"] == {
        "status": "unavailable", "result": None}
    unconfigured = TestClient(create_app(ready.docs.settings))
    assert unconfigured.get("/api/v1/measurement/status").json()["data"] == {"status": "disabled"}
    for client in (connected, unconfigured):
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/companies").status_code == 200
    _, _, dataset, _ = Indexes(ready.docs).validate(ready.index_id)
    chunk = next(row["id"] for row in dataset["rows"] if row["corpus_type"] == "annual_report")
    submit = connected.post("/api/v1/retrieval-benchmarks", json={
        "name": "测度服务离线检索", "index_id": ready.index_id, "methods": ["bm25"],
        "cases": [{"key": "d2", "dimension": "D2", "query": "审计委员会", "stock_code": "000001",
                   "report_year": 2024, "relevant_chunk_ids": [chunk]}]})
    assert submit.status_code == 202
    assert work_once(ready.docs)["status"] == "succeeded"
    job_id = submit.json()["data"]["id"]
    report = connected.get(f"/api/v1/retrieval-benchmarks/{job_id}/report?format=md")
    assert report.status_code == 200 and "召回率" in report.text
