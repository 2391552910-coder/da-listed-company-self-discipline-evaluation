"""
交叉编码重排器（reranker）

默认候选 Qwen3-Reranker-0.6B，用于混合检索后的重排。Transformers
实现使用官方指令模板和因果语言模型 yes/no logits；真实权重实测与
数据集级别评估仍须单独记录，单元测试不能代替实验结果。

本项目本地 Embedding 与 Reranker 均限定 0.6B；默认 CUDA，不自动回退 CPU。
权重精度、batch、截断与峰值内存须实测，不以理论权重大小保证主机可运行。

模型部署在另一台机器时，用 APIReranker 走 HTTP 调用，接口与本地
Reranker 完全一致（score(query, chunks) -> list[float]），自动适配
Jina/Xinference/vLLM 的 {"results":[{index, relevance_score}]} 与
TEI 的 [{index, score}] 两种返回格式。
"""
import json
import urllib.request

from app.research_adapter.vendor.data.clause_splitter import Chunk
from app.research_adapter.vendor.retrieval.runtime import model_load_options

DEFAULT_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
SYSTEM_PREFIX = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the Instruct provided.\n"
    "Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n"
    "<|im_start|>user\n"
)
ASSISTANT_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def format_instruction(instruction: str | None, query: str, document: str) -> str:
    instruction = instruction or DEFAULT_INSTRUCTION
    return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"


def build_reranker_inputs(tokenizer, pairs: list[str], max_length: int,
                          prefix_tokens: list[int] | None = None,
                          suffix_tokens: list[int] | None = None):
    """Tokenize content using the official Qwen3 yes/no reranker prompt layout."""
    prefix_tokens = prefix_tokens if prefix_tokens is not None else tokenizer.encode(
        SYSTEM_PREFIX, add_special_tokens=False)
    suffix_tokens = suffix_tokens if suffix_tokens is not None else tokenizer.encode(
        ASSISTANT_SUFFIX, add_special_tokens=False)
    content_budget = max_length - len(prefix_tokens) - len(suffix_tokens)
    if content_budget < 1:
        raise ValueError("max_length cannot fit the Qwen3 reranker prefix, suffix, and content")
    encoded = tokenizer(
        pairs,
        padding=False,
        truncation="longest_first",
        return_attention_mask=False,
        max_length=content_budget,
    )
    rows = [prefix_tokens + ids + suffix_tokens for ids in encoded["input_ids"]]
    return tokenizer.pad({"input_ids": rows}, padding=True, return_tensors="pt")


class Reranker:
    def __init__(self, model_name: str = "Qwen/Qwen3-Reranker-0.6B",
                 device: str = "cuda", max_length: int = 3072, batch_size: int = 1,
                 revision: str | None = None, instruction: str = DEFAULT_INSTRUCTION,
                 local_files_only: bool = False, dtype: str = "float16"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        if max_length < 1 or batch_size < 1:
            raise ValueError("max_length and batch_size must be positive")
        self._torch = torch
        model_options = model_load_options(device, dtype)
        self.device = torch.device(device)
        self._tok = AutoTokenizer.from_pretrained(
            model_name, revision=revision, padding_side="left",
            local_files_only=local_files_only)
        if self._tok.pad_token_id is None:
            self._tok.pad_token = self._tok.eos_token
        self._model = AutoModelForCausalLM.from_pretrained(
            model_name, revision=revision, local_files_only=local_files_only,
            **model_options)
        self._model.to(self.device)
        self._model.eval()
        self.max_length = max_length
        self.batch_size = batch_size
        self.revision = revision
        self.instruction = instruction or DEFAULT_INSTRUCTION
        self._prefix_tokens = self._tok.encode(SYSTEM_PREFIX, add_special_tokens=False)
        self._suffix_tokens = self._tok.encode(ASSISTANT_SUFFIX, add_special_tokens=False)
        no_ids = self._tok.encode("no", add_special_tokens=False)
        yes_ids = self._tok.encode("yes", add_special_tokens=False)
        if len(no_ids) != 1 or len(yes_ids) != 1:
            raise ValueError("Qwen3 reranker yes/no labels must each be a single token")
        self._token_false_id, self._token_true_id = no_ids[0], yes_ids[0]
        if self._token_false_id == self._token_true_id:
            raise ValueError("Qwen3 reranker yes/no token IDs must differ")
        if max_length <= len(self._prefix_tokens) + len(self._suffix_tokens):
            raise ValueError("max_length cannot fit the Qwen3 reranker prompt")
        self.metadata = {
            "backend": "transformers_qwen3_yes_no_logits",
            "model": model_name,
            "revision": revision,
            "device": str(self.device),
            "dtype": str(next(self._model.parameters()).dtype),
            "max_length": max_length,
            "batch_size": batch_size,
            "instruction": self.instruction,
            "padding_side": self._tok.padding_side,
            "score": "softmax_probability_yes_over_no",
        }

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        """返回与 chunks 等长的相关性分数列表"""
        scores: list[float] = []
        for i in range(0, len(chunks), self.batch_size):
            batch = chunks[i:i + self.batch_size]
            pairs = [format_instruction(self.instruction, query, c.text) for c in batch]
            enc = build_reranker_inputs(
                self._tok, pairs, self.max_length, self._prefix_tokens, self._suffix_tokens)
            enc = {key: value.to(self.device) for key, value in enc.items()}
            with self._torch.inference_mode():
                logits = self._model(**enc).logits[:, -1, :]
                no_logits = logits[:, self._token_false_id]
                yes_logits = logits[:, self._token_true_id]
                binary_logits = self._torch.stack([no_logits, yes_logits], dim=-1)
                batch_scores = self._torch.softmax(binary_logits, dim=-1)[:, 1]
            scores.extend(batch_scores.float().cpu().tolist())
        return scores


class APIReranker:
    """远端重排服务客户端（Xinference / vLLM / TEI / Jina 兼容）"""

    def __init__(self, base_url: str, model_name: str = "Qwen3-Reranker-0.6B",
                 api_key: str = "", batch_size: int = 32):
        # base_url 指到服务根（如 http://192.168.1.10:9997/v1），自动拼 /rerank
        self._url = base_url.rstrip("/") + "/rerank"
        self._model = model_name
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self.batch_size = batch_size

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        scores: list[float] = []
        for i in range(0, len(chunks), self.batch_size):
            docs = [c.text for c in chunks[i:i + self.batch_size]]
            payload = json.dumps(
                {"model": self._model, "query": query, "documents": docs}).encode()
            req = urllib.request.Request(self._url, data=payload, headers=self._headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            # 两种主流返回：{"results":[{index, relevance_score}]} 或 [{index, score}]
            items = data["results"] if isinstance(data, dict) else data
            batch_scores = [0.0] * len(docs)
            for it in items:
                batch_scores[it["index"]] = float(it.get("relevance_score", it.get("score", 0.0)))
            scores.extend(batch_scores)
        return scores
