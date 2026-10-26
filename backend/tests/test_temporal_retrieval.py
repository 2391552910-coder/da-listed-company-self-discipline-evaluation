"""Temporal selection must constrain recall pools, not filter truncated hits.

The annual parser is a unit fixture; SQLite persistence, artifact builds and
BM25 query subprocesses are real. The Qwen entry test replaces only its model
retriever to inspect its input pool without requiring CUDA in CI.
"""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from app.documents.common import DomainError, dump, sha
from app.documents.indexes import Indexes
from app.documents.parsing import parse_file
from app.documents.service import Documents
from app.documents.settings import Settings
from app.documents.worker import work_once


@pytest.fixture
def temporal_index(tmp_path_factory, monkeypatch):
    tmp_path = tmp_path_factory.mktemp('tr')
    docs = Documents(Settings("sqlite:///" + str(tmp_path / "temporal.sqlite"),
                              tmp_path / "assets", sys.executable))
    docs.db.migrate()
    parse_ids = []
    versions = {}

    def fixture_parser(data, file_format, config, checkpoint=None):
        if file_format != "pdf":
            return parse_file(data, file_format, config, checkpoint=checkpoint)
        text = data.split(b"\n", 1)[1].decode()
        return ([{"physical_page": 1, "text": text, "text_sha256": sha(text.encode()),
                  "text_origin": "unit_fixture", "spans": [(0, len(text), text)]}],
                {"name": "unit_fixture", "version": "1", "opening": text}, [])

    monkeypatch.setattr("app.documents.service.parse_file", fixture_parser)
    for stock, year, label in [("000001", 2024, "target"),
                               ("000002", 2024, "other_company"),
                               ("000001", 2023, "other_year")]:
        company = docs.company(stock, "Fixture Corporation")
        text = f"Fixture Corporation {stock} {year}年年度报告 audit governance"
        version = docs.upload(b"%PDF-1.4\n" + text.encode(), label + ".pdf",
                              "annual_report", label, company["id"], year)
        versions[label] = version["id"]
        docs.enqueue_parse(version["id"])
        job = work_once(docs)
        assert job["status"] == "succeeded", job
        parse_ids.append(job["result"]["parse_id"])

    law_specs = [
        ("Expired-" + str(i), "audit " * 20,
         {"effective_from": "2020-01-01", "effective_to_exclusive": "2024-01-01"})
        for i in range(7)
    ] + [
        ("Current-low-score", "audit " + "governance compliance obligations " * 25,
         {"effective_from": "2024-01-01", "effective_to_exclusive": "2025-01-01"}),
        ("Unknown", "audit " + "governance compliance " * 10, {}),
    ]
    for title, text, metadata in law_specs:
        version = docs.upload(text.encode(), title + ".txt", "regulation",
                              title, metadata=metadata)
        versions[title] = version["id"]
        docs.enqueue_parse(version["id"])
        job = work_once(docs)
        assert job["status"] == "succeeded", job
        parse_ids.append(job["result"]["parse_id"])

    indexes = Indexes(docs)
    indexes.enqueue(parse_ids, "bm25_diagnostic", False)
    built = work_once(docs)
    assert built["status"] == "succeeded", built
    return docs, indexes, built["result"]["index_id"], versions


def filtered_search(indexes, index_id, *, unknown="exclude", target="2024-07-01", top_k=20):
    return indexes.search(index_id, "audit", "000001", 2024, top_k,
                          temporal_filter=True, target_date=target,
                          target_date_role="event_date", unknown_temporal=unknown)


def test_bm25_temporal_pool_precedes_topk_truncation(temporal_index):
    _, indexes, index_id, _ = temporal_index
    baseline = indexes.search(index_id, "audit", "000001", 2024, 1)
    # This fixture deliberately buries the current source below all short,
    # high-frequency expired sources. Filtering returned top1 would lose it.
    assert baseline["regulation"][0]["title"].startswith("Expired-")
    result = filtered_search(indexes, index_id, top_k=1)
    assert [hit["title"] for hit in result["regulation"]] == ["Current-low-score"]


@pytest.mark.parametrize("unknown, expected", [
    ("retain", {"Current-low-score", "Unknown"}),
    ("exclude", {"Current-low-score"}),
])
def test_bm25_unknown_policy_preserves_correct_scope(temporal_index, unknown, expected):
    docs, indexes, index_id, versions = temporal_index
    result = filtered_search(indexes, index_id, unknown=unknown)
    assert {hit["title"] for hit in result["regulation"]} == expected
    assert result["annual_report"]
    assert {hit["version_id"] for hit in result["annual_report"]} == {versions["target"]}
    for hit in result["annual_report"] + result["regulation"]:
        assert docs.source(hit["chunk_id"])["content"] == hit["content"]


def test_empty_temporal_regulation_pool_does_not_fall_back_to_all(temporal_index):
    _, indexes, index_id, versions = temporal_index
    result = filtered_search(indexes, index_id, target="2030-01-01")
    assert result["regulation"] == []
    assert result["annual_report"]
    assert {hit["version_id"] for hit in result["annual_report"]} == {versions["target"]}


def test_temporal_filter_does_not_expand_missing_annual_scope(temporal_index):
    _, indexes, index_id, _ = temporal_index
    with pytest.raises(DomainError) as caught:
        indexes.search(index_id, "audit", "000002", 2030, 3,
                       temporal_filter=True, target_date="2024-07-01",
                       target_date_role="event_date", unknown_temporal="retain")
    assert caught.value.code == "ANNUAL_SCOPE_EMPTY"


def test_qwen_retriever_receives_prefiltered_dense_pool(temporal_index, tmp_path, monkeypatch):
    import numpy as np
    from app.research_adapter import runner

    _, indexes, index_id, versions = temporal_index
    _, _, dataset, _ = indexes.validate(index_id)
    directory = tmp_path / "qwen-entry-contract"
    directory.mkdir()
    dataset["config"]["rerank"] = False
    data_bytes = dump(dataset).encode()
    (directory / "dataset.json").write_bytes(data_bytes)
    matrix = np.zeros((len(dataset["rows"]), 4), dtype=np.float32)
    matrix[:, 0] = 1
    np.save(directory / "vectors.npy", matrix, allow_pickle=False)
    identity = {"fixture": "shared-static-encoder"}
    manifest = {
        "format": "dalun-source-index-v1", "profile": "qwen_cuda",
        "dataset_sha256": runner.digest(data_bytes),
        "vector_sha256": runner.digest((directory / "vectors.npy").read_bytes()),
        "vector_shape": list(matrix.shape), "encoder_identity": identity,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls = []

    class InspectDensePool:
        def __init__(self, chunks, **kwargs):
            self.dense = SimpleNamespace(encoding_metadata=identity)

        def search(self, query, top_k, pool=None, trace=None):
            assert pool is not None
            roles = {chunk.meta["corpus_type"] for chunk in pool}
            assert len(roles) == 1
            role = roles.pop()
            calls.append((role, [chunk.doc_id for chunk in pool]))
            expected = versions["target"] if role == "annual_report" else versions["Current-low-score"]
            # The dense/hybrid entry itself must never receive expired,
            # unknown-excluded or other-company/year candidates.
            assert pool and {chunk.doc_id for chunk in pool} == {expected}
            hits = [(chunk, 1.0) for chunk in pool[:top_k]]
            if trace:
                trace.finish(hits)
            return hits

    monkeypatch.setattr("app.research_adapter.vendor.retrieval.hybrid.HybridRetriever", InspectDensePool)
    request = {
        "query": "audit", "stock_code": "000001", "report_year": 2024, "top_k": 1,
        "temporal_filter": True, "target_date": "2024-07-01",
        "target_date_role": "event_date", "unknown_temporal": "exclude",
    }
    request_file, response_file = directory / "request.json", directory / "response.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    runner.query(directory, request_file, response_file)
    result = json.loads(response_file.read_bytes())
    assert len(calls) == 2
    assert result["regulation"] and result["annual_report"]
