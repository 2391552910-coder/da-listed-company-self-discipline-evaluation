"""
稠密向量检索

默认 Qwen3-Embedding（sentence-transformers 本地加载，归一化后点积=余弦）。
embedding_backend=bge 时兼容 BGE-M3；embedding_backend=tfidf 时降级为
TF-IDF 余弦（供无法下载模型时的冒烟测试）；embedding_backend=api 时调用
远端 OpenAI 兼容嵌入服务（vLLM / Xinference / Ollama 的 /v1/embeddings），
适合模型部署在另一台机器、本机只做评测的场景。

"""
import json
import hashlib
import urllib.request

import numpy as np

from app.research_adapter.vendor.data.clause_splitter import Chunk
from app.research_adapter.vendor.retrieval.runtime import model_load_options


def _api_embed(texts: list[str], base_url: str, model: str,
               api_key: str = "", batch_size: int = 32) -> np.ndarray:
    """调用 OpenAI 兼容 /embeddings 接口，返回归一化向量矩阵"""
    url = base_url.rstrip("/") + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        payload = json.dumps({"model": model, "input": texts[i:i + batch_size]}).encode()
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        vecs.extend(d["embedding"] for d in sorted(data["data"], key=lambda x: x["index"]))
    arr = np.asarray(vecs, dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True).clip(min=1e-12)


class DenseRetriever:
    def __init__(self, chunks: list[Chunk], backend: str = "bge",
                 model_name: str = "BAAI/bge-m3", device: str = "cuda",
                 base_url: str = "", api_key: str = "", batch_size: int = 32,
                 query_prompt: str = "", max_seq_length: int | None = None,
                 revision: str | None = None, local_files_only: bool = False,
                 dtype: str | None = None,
                 precomputed_vectors: np.ndarray | None = None):
        if batch_size < 1:
            raise ValueError("batch_size 必须为正数")
        if max_seq_length is not None and max_seq_length < 1:
            raise ValueError("max_seq_length 必须为正数")
        self.chunks = chunks
        self.backend = backend
        self.query_prompt = query_prompt if backend != "tfidf" else ""
        self.encoding_metadata = {"backend": backend, "batch_size": batch_size,
                                  "model_name": model_name,
                                  "requested_revision": revision,
                                  "requested_dtype": dtype,
                                  "query_prompt": self.query_prompt,
                                  "query_prompt_sha256": hashlib.sha256(self.query_prompt.encode()).hexdigest(),
                                  "document_prompt": "", "normalize_embeddings": True,
                                  "model_revision": revision if backend in ("bge", "qwen") else None}
        if backend in ("bge", "qwen"):
            model_options = model_load_options(device, dtype)
            # sentence-transformers 统一加载：BGE-M3 / 本项目固定 Qwen3-Embedding-0.6B
            # 查询指令显式配置；文档不添加指令，也不继承模型的默认 prompt。
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name, device=device, revision=revision,
                                              local_files_only=local_files_only,
                                              model_kwargs=model_options)
            if max_seq_length is not None:
                if max_seq_length > self._model.max_seq_length:
                    raise ValueError("max_seq_length 超过模型支持的长度")
                self._model.max_seq_length = max_seq_length
            self.encoding_metadata.update(max_seq_length=self._model.max_seq_length,
                                          device=str(self._model.device),
                                          model_dtype=str(next(self._model.parameters()).dtype))
            first_module_fn = getattr(self._model, "_first_module", None)
            first_module = first_module_fn() if callable(first_module_fn) else None
            model_config = getattr(getattr(first_module, "auto_model", None), "config", None)
            self.encoding_metadata["resolved_revision"] = getattr(model_config, "_commit_hash", None)
            encode_documents = lambda texts: self._model.encode(
                texts, prompt="", normalize_embeddings=True, batch_size=batch_size)
            self._encode = lambda texts: self._model.encode(
                texts, prompt=self.query_prompt, normalize_embeddings=True, batch_size=batch_size)
        elif backend == "tfidf":
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.preprocessing import normalize as sp_normalize
            from app.research_adapter.vendor.retrieval.bm25 import tokenize
            # 限制特征上限 + 保持稀疏矩阵，避免大规模语料下稠密化 OOM
            self._vectorizer = TfidfVectorizer(tokenizer=tokenize,
                                               max_features=60000, min_df=2)
            corpus = [c.text for c in chunks]
            self._matrix = sp_normalize(self._vectorizer.fit_transform(corpus))
            self._sparse = True

            def _encode(texts):
                return sp_normalize(self._vectorizer.transform(texts))
            self._encode = _encode
        elif backend == "api":
            if not base_url:
                raise ValueError("api 后端需传 base_url，如 http://192.168.1.10:8000/v1")
            encode_documents = lambda texts: _api_embed(
                texts, base_url, model_name, api_key, batch_size)
            self._encode = lambda texts: encode_documents([self.query_prompt + t for t in texts])
        else:
            raise ValueError(f"未知嵌入后端: {backend}")
        self._sparse = backend == "tfidf"

        if precomputed_vectors is not None:
            if backend not in ("bge", "qwen", "api"):
                raise ValueError("Precomputed dense vectors require a dense embedding backend")
            matrix = np.asarray(precomputed_vectors)
            if matrix.ndim != 2 or matrix.shape[0] != len(chunks):
                raise ValueError("Precomputed vector rows must match the document chunks")
            if not np.isfinite(matrix).all():
                raise ValueError("Precomputed vectors contain non-finite values")
            self._matrix = matrix
        elif backend in ("bge", "qwen", "api"):
            self._matrix = encode_documents([c.text for c in chunks])

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        """按当前查询指令与批量设置编码查询；文档向量使用独立的无指令路径。"""
        return self._encode(texts)

    def search(self, query: str, top_k: int = 5,
               candidate_ids: set[str] | None = None) -> list[tuple[Chunk, float]]:
        if candidate_ids is not None and not candidate_ids:
            return []
        q_vec = self._encode([query])
        if self._sparse:
            scores = (self._matrix @ q_vec.T).toarray().ravel()
        else:
            scores = self._matrix @ q_vec[0]
        if candidate_ids is None:
            candidate_indices = np.arange(len(self.chunks))
        else:
            candidate_indices = np.fromiter(
                (i for i, chunk in enumerate(self.chunks) if chunk.chunk_id in candidate_ids),
                dtype=np.int64,
            )
        if candidate_indices.size == 0:
            return []
        idx = candidate_indices[np.argsort(-scores[candidate_indices])[:top_k]]
        return [(self.chunks[i], float(scores[i])) for i in idx]
