"""Selectable retrieval methods and their runtime requirements."""
from app.documents.common import DomainError


METHODS = {
    "bm25": "BM25",
    "dense": "向量检索",
    "rrf": "BM25 + 向量检索（RRF）",
    "bm25_rerank": "BM25 + 重排",
    "rrf_rerank": "BM25 + 向量检索（RRF）+ 重排",
}
RRF_K = 60


def validate_method(profile, method):
    if method is None:
        return
    if method not in METHODS:
        raise DomainError("INVALID_RETRIEVAL_METHOD", "未知检索方法", 422)
    if method != "bm25" and profile != "qwen_cuda":
        raise DomainError("RETRIEVAL_METHOD_UNAVAILABLE", "该方法需要 Qwen CUDA 索引及对应本地模型", 422)


class SelectedRetriever:
    def __init__(self, method, sparse, dense=None, reranker=None):
        self.method = method
        self.sparse = sparse
        self.dense = dense
        self.reranker = reranker

    def search(self, query, pool, top_k, candidate_k):
        ids = {c.chunk_id for c in pool}
        if self.method == "dense":
            candidates = self.dense.search(query, candidate_k, candidate_ids=ids)
        else:
            candidates = self.sparse.search(query, candidate_k, candidate_ids=ids)
            if self.method in {"rrf", "rrf_rerank"}:
                scores, by_id = {}, {}
                dense = self.dense.search(query, candidate_k, candidate_ids=ids)
                for channel in (candidates, dense):
                    for rank, (chunk, _) in enumerate(channel, 1):
                        key = chunk.chunk_id
                        by_id[key] = chunk
                        scores[key] = scores.get(key, 0) + 1 / (RRF_K + rank)
                order = sorted(scores, key=lambda key: (-scores[key], key))[:candidate_k]
                candidates = [(by_id[key], scores[key]) for key in order]
        if self.reranker is not None and candidates:
            chunks = [chunk for chunk, _ in candidates]
            scores = self.reranker.score(query, chunks)
            if len(scores) != len(chunks):
                raise ValueError("Reranker did not return one score per candidate")
            candidates = sorted(zip(chunks, scores), key=lambda hit: (-hit[1], hit[0].chunk_id))
        return candidates[:top_k]
