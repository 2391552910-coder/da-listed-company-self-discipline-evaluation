from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.documents.api import create_app
from app.documents.common import DomainError
from app.documents.indexes import Indexes
from app.documents.worker import work_once
from app.experiments.retrieval_benchmark import RetrievalBenchmark, metrics, report
from app.retrieval.methods import SelectedRetriever
from test_context_service import ready


def test_frozen_bm25_benchmark_runs_in_worker_and_exports(ready):
    index = Indexes(ready.docs)
    _, _, dataset, _ = index.validate(ready.index_id)
    chunk = next(row for row in dataset["rows"] if row["corpus_type"] == "annual_report")
    payload = {"name": "同题检索对照", "index_id": ready.index_id,
        "top_k": 5, "candidate_k": 10, "max_runtime_seconds": 60, "methods": ["bm25"],
        "cases": [{"key": "D2-01", "query": "审计委员会", "stock_code": "000001.SZ", "report_year": 2024,
                   "dimension": "D2", "relevant_chunk_ids": [chunk["id"]], "irrelevant_chunk_ids": []}]}
    benchmark = RetrievalBenchmark(ready.docs)
    client = TestClient(create_app(ready.docs.settings))
    response = client.post("/api/v1/retrieval-benchmarks", json=payload,
                           headers={"Idempotency-Key": "benchmark-fixture"})
    assert response.status_code == 202
    task = response.json()["data"]
    assert benchmark.enqueue(payload, "benchmark-fixture")["id"] == task["id"]
    assert task["status"] == "queued"
    finished = work_once(ready.docs)
    assert finished["status"] == "succeeded", finished
    output = benchmark.get(task["id"])["result"]
    assert output["rows"][0]["ranking"] == [chunk["id"]]
    assert output["summary"][0]["recall"] == 1
    assert output["summary"][0]["precision"] == .2
    assert output["summary"][0]["judged_accuracy"] is None
    assert "20.00%" in report(output, "md")[0]
    assert "bm25,D2" in report(output, "csv")[0]
    download = client.get(f'/api/v1/retrieval-benchmarks/{task["id"]}/report?format=md')
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert "20.00%" in download.text
    with pytest.raises(DomainError) as error:
        benchmark.enqueue(payload | {"methods": ["dense"]})
    assert error.value.code == "RETRIEVAL_METHOD_UNAVAILABLE"
    with pytest.raises(DomainError) as error:
        benchmark.enqueue(payload | {"cases": [payload["cases"][0] | {"report_year": 2023}]})
    assert error.value.code == "INVALID_JUDGMENT_SCOPE"


def test_rank_fusion_and_rerank_keep_scope_and_candidate_limits():
    a, b, c = [SimpleNamespace(chunk_id=i) for i in "abc"]

    class Channel:
        def __init__(self, order):
            self.order = order

        def search(self, query, k, candidate_ids):
            return [(x, 1.0) for x in self.order if x.chunk_id in candidate_ids][:k]

    sparse = Channel([a, b])
    dense = Channel([c, b])
    calls = []

    def ranker(query, candidates):
        calls.append([x.chunk_id for x in candidates])
        return [float(x.chunk_id == "a") for x in candidates]

    rerank = SimpleNamespace(score=ranker)
    assert SelectedRetriever("dense", sparse, dense).search("q", [a, b], 1, 2)[0][0] == b
    assert SelectedRetriever("rrf", sparse, dense).search("q", [a, b, c], 1, 2)[0][0] == b
    assert SelectedRetriever("bm25_rerank", sparse, reranker=rerank).search("q", [a, b, c], 1, 2)[0][0] == a
    assert SelectedRetriever("rrf_rerank", sparse, dense, rerank).search("q", [a, b, c], 1, 2)[0][0] == a
    assert calls == [["a", "b"], ["b", "a"]]
    assert metrics(["unknown", "a"], {"a", "b"}, {"c"}, 2) == {
        "precision": .5, "recall": .5, "hit_rate": 1, "mrr": .5, "judged_accuracy": 2 / 3,
        "matched_relevant": 1, "known_relevant": 2, "unjudged_returned": 1}
