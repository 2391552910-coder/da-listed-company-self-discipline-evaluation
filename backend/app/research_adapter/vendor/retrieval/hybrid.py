"""
混合检索：BM25 稀疏 + 稠密向量，分数归一化后加权融合重排
"""
from app.research_adapter.vendor.data.clause_splitter import Chunk
from app.research_adapter.vendor.retrieval.bm25 import BM25Retriever
from app.research_adapter.vendor.retrieval.dense import DenseRetriever
from app.research_adapter.vendor.retrieval.search_trace import hit_rows, score_value


class HybridRetriever:
    def __init__(self, chunks: list[Chunk], vector_weight: float = 0.7,
                 reranker=None, rerank_pool_factor: int = 4,
                 fusion: str = "weighted", rrf_k: int = 60,
                 document_vectors=None, **dense_kw):
        if fusion not in {"weighted", "rrf"}:
            raise ValueError("fusion must be 'weighted' or 'rrf'")
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self.chunks = chunks
        self.vector_weight = vector_weight
        self.fusion = fusion
        self.rrf_k = rrf_k
        self.reranker = reranker          # 交叉编码精排器（None=不重排）
        self.rerank_pool_factor = rerank_pool_factor
        self.bm25 = BM25Retriever(chunks)
        self.dense = DenseRetriever(chunks, precomputed_vectors=document_vectors,
                                    **dense_kw)

    @staticmethod
    def _normalize(hits: list[tuple[Chunk, float]]) -> dict[str, float]:
        if not hits:
            return {}
        max_s = max(s for _, s in hits) or 1.0
        return {c.chunk_id: s / max_s for c, s in hits}

    def _reciprocal_rank_scores(self, *hit_lists: list[tuple[Chunk, float]]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for hits in hit_lists:
            for rank, (chunk, _) in enumerate(hits, start=1):
                scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1 / (self.rrf_k + rank)
        return scores

    def search(self, query: str, top_k: int = 5,
               pool: list[Chunk] | None = None, *, trace=None) -> list[tuple[Chunk, float]]:
        """
        pool 不为空时只在候选子库内检索（维度路由使用），
        为空时在全库检索。
        """
        candidates = self.chunks if pool is None else pool
        if trace is not None:
            trace.emit('hybrid_configured', fusion_policy={'fusion': self.fusion,
                'vector_weight': self.vector_weight, 'rrf_k': self.rrf_k,
                'rerank_pool_factor': self.rerank_pool_factor}, rerank_enabled=self.reranker is not None)
        if not candidates:
            if trace is not None: trace.finish([])
            return []
        by_id = {c.chunk_id: c for c in candidates}
        candidate_ids = set(by_id)

        # Rank within the requested corpus scope before truncating each retriever's pool.
        if trace is not None: trace.emit('dense_call_started')
        vec_hits = self.dense.search(query, top_k=top_k * 2, candidate_ids=candidate_ids)
        if trace is not None: trace.emit('dense_returned', dense_returned=hit_rows(vec_hits))
        if trace is not None: trace.emit('bm25_call_started')
        kw_hits = self.bm25.search(query, top_k=top_k * 2, candidate_ids=candidate_ids)
        if trace is not None: trace.emit('bm25_returned', bm25_returned=hit_rows(kw_hits))

        excluded_dense = [c.chunk_id for c, _ in vec_hits if c.chunk_id not in by_id]
        excluded_bm25 = [c.chunk_id for c, _ in kw_hits if c.chunk_id not in by_id]
        vec_hits = [(c, s) for c, s in vec_hits if c.chunk_id in by_id]
        kw_hits = [(c, s) for c, s in kw_hits if c.chunk_id in by_id]
        if trace is not None:
            trace.emit('channel_scopes_applied', dense_candidates=hit_rows(vec_hits), bm25_candidates=hit_rows(kw_hits),
                       dense_excluded_scope_ids=excluded_dense, bm25_excluded_scope_ids=excluded_bm25)
        if self.fusion == "rrf":
            fused = self._reciprocal_rank_scores(vec_hits, kw_hits)
        else:
            vec_norm = self._normalize(vec_hits)
            kw_norm = self._normalize(kw_hits)
            fused = {}
            for cid, s in vec_norm.items():
                fused[cid] = fused.get(cid, 0.0) + s * self.vector_weight
            for cid, s in kw_norm.items():
                fused[cid] = fused.get(cid, 0.0) + s * (1 - self.vector_weight)

        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        prefix = ranked[:top_k * self.rerank_pool_factor]
        if trace is not None:
            trace.emit('fusion_returned', fused_candidates=hit_rows([(by_id[cid], s) for cid, s in ranked]),
                       rerank_prefix=hit_rows([(by_id[cid], s) for cid, s in prefix]))

        # 交叉编码精排：取融合分前 top_k * rerank_pool_factor 名，用 reranker 重打分
        if self.reranker is not None and ranked:
            rerank_pool = prefix
            if trace is not None:
                trace.emit('rerank_call_started', rerank_inputs=hit_rows([(by_id[cid], s) for cid, s in rerank_pool]))
            rr_scores = self.reranker.score(query, [by_id[cid] for cid, _ in rerank_pool])
            if trace is not None: trace.emit('rerank_returned', rerank_scores=[score_value(s) for s in rr_scores])
            if len(rr_scores) != len(rerank_pool):
                raise ValueError("reranker score count must match the candidate count")
            ranked = sorted(zip((cid for cid, _ in rerank_pool), rr_scores),
                            key=lambda x: x[1], reverse=True)
            if trace is not None:
                trace.emit('rerank_sorted', reranked_candidates=hit_rows([(by_id[cid], s) for cid, s in ranked]))

        hits = [(by_id[cid], s) for cid, s in ranked[:top_k]]
        if trace is not None: trace.finish(hits)
        return hits
