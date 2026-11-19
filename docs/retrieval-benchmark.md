# 年报检索对照和报告

在「实验与对照」的「检索方法对照」选一个就绪索引，上传或粘贴人工标注的题集 JSON，选择方法、返回 Top K、每路候选上限和任务时限。后台 Worker 基于同一冻结索引、公司年度和题目运行各方法。完成后可下载 Markdown 报告、CSV 指标和完整 JSON。任务中心可以重新打开已提交的对照。

| 方法 ID | 检索过程 | 所需环境 |
| --- | --- | --- |
| `bm25` | BM25 排序 | 任一种就绪索引 |
| `dense` | Qwen 向量检索 | Qwen CUDA 索引和本地 Embedding |
| `rrf` | BM25 和向量结果的 RRF 融合，RRF 常数 60 | Qwen CUDA 索引和本地 Embedding |
| `bm25_rerank` | Qwen 重排 BM25 候选 | Qwen CUDA 索引和本地 Reranker |
| `rrf_rerank` | Qwen 重排 RRF 融合候选 | Qwen CUDA 索引和本地 Embedding、Reranker |

返回 Top K 为 1 至 20，候选上限为 K 至 100，默认 10 和 50。候选上限也用于融合后的重排候选池。一次最多运行 100 个题目与方法组合。沿用现有双源建库要求，指标只统计年报片段。

问答实验「共同检索、模型与总调用预算」也可选择上述方法，固定与 Agent 共用方法。保留默认设置时沿用索引策略。语义正确性沿用原实验的独立评分记录。第三章自律指数依赖另行提供的指标数据和参数。

## 题集格式

片段 ID 来自 `GET /api/v1/document-versions/{version_id}/chunks` 或检索结果的 `chunk_id`。ID 必须属于冻结索引及题目指定的公司年度。相关片段至少标一项，负例可留空。变更索引或切片后，须重新映射相关标注。

```json
[
  {
    "key": "D2-01",
    "dimension": "D2",
    "query": "报告期内审计委员会召开几次会议？",
    "stock_code": "000001",
    "report_year": 2024,
    "relevant_chunk_ids": ["实际相关片段ID"],
    "irrelevant_chunk_ids": ["实际不相关片段ID"]
  }
]
```

## 指标口径

- 精确率@K = 前 K 中命中的人工正例数 / K。标注不完整时，仅统计已知正例。
- 召回率@K = 前 K 中命中的人工正例数 / 人工正例总数。
- 命中率@K = 至少命中一个正例的题目比例。MRR 是首个正例倒数名次的均值。
- 已标注准确率 = （检出的人工正例 + 未检出的人工负例）/ 人工正负例总数。仅在提供负例时计算，未标注片段不参与该指标。
- 总体和维度结果对成功题目做宏平均，展示计划数和已评测数。失败项保留错误码。JSON 报告包含冻结协议与索引哈希、方法、逐题排名、耗时和未判定片段数。

## HTTP 接口

- `POST /api/v1/retrieval-benchmarks` 创建后台任务。请求包含 `name`、`index_id`、`methods`、`top_k`、`candidate_k`、`max_runtime_seconds` 和 `cases`，支持 `Idempotency-Key`。
- `GET /api/v1/retrieval-benchmarks/{job_id}` 查看运行结果。
- `GET /api/v1/retrieval-benchmarks/{job_id}/report?format=md` 下载报告，也可选择 `csv` 和 `json`。
