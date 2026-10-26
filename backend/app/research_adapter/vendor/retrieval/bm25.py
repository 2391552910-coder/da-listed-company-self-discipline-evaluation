"""
BM25 稀疏检索（自实现，jieba 分词）

BM25 负责精确术语召回（法规名称、条款号、处罚文号等），
与稠密向量检索互补。
"""
import math
from collections import Counter

from app.research_adapter.vendor.data.clause_splitter import Chunk

try:
    import jieba
    _HAS_JIEBA = True
except ImportError:  # 离线环境降级：字符 bigram 分词
    _HAS_JIEBA = False


def tokenize(text: str) -> list[str]:
    if _HAS_JIEBA:
        return [t for t in jieba.cut(text) if t.strip()]
    text = text.strip()
    return [text[i:i + 2] for i in range(len(text) - 1)]


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self._tokens = [tokenize(c.text) for c in chunks]
        df: Counter = Counter()
        for tokens in self._tokens:
            df.update(set(tokens))
        n = len(self._tokens)
        self._avgdl = sum(len(t) for t in self._tokens) / max(n, 1)
        self._idf = {t: math.log((n - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}

    def search(self, query: str, top_k: int = 5,
               candidate_ids: set[str] | None = None) -> list[tuple[Chunk, float]]:
        if candidate_ids is not None and not candidate_ids:
            return []
        q_tokens = tokenize(query)
        scored: list[tuple[int, float]] = []
        for idx, tokens in enumerate(self._tokens):
            if candidate_ids is not None and self.chunks[idx].chunk_id not in candidate_ids:
                continue
            tf = Counter(tokens)
            dl = len(tokens)
            score = sum(
                self._idf.get(t, 0) * tf.get(t, 0) * (self.k1 + 1)
                / (tf.get(t, 0) + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
                for t in q_tokens
            )
            if score > 0:
                scored.append((idx, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(self.chunks[i], s) for i, s in scored[:top_k]]
