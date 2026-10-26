# 阶段 3 双源上下文与任务控制

日期：2026-10-26。阶段 3 实现与验证已完成：真实 CUDA 上下文闭环、PostgreSQL 增量迁移、HTTP/数据库测试和前端生产构建均已通过。完整服务器部署与实际浏览器交互仍留待系统级验收。

## 本阶段范围

| 工作包 | 实现 | 主要代码 |
| --- | --- | --- |
| S3.1 双源检索与预算 | 公司年度隔离、来源独立配额、可选时点选择、完整片段组装、A/R 编号、实际计数和不可变快照 | backend/app/retrieval/、backend/app/documents/indexes.py、backend/app/research_adapter/runner.py |
| S3.2 任务与 Worker | 排队与进度、幂等提交、取消、新身份重试、租约与 owner fencing、attempt 日志及快照恢复 | backend/app/documents/jobs.py、service.py、worker.py |

本阶段产物是“可审查的候选与实际上下文”，尚不生成公司评价或判断论断是否得到语义支持。第三章指数结果、四维生成评价、报告生成及工具 Agent 留在后续阶段。

## 双源检索与完整片段预算

年报候选在指定公司和报告年度内排序，法规候选在独立法规作用域内排序。annual_top_k 和 regulation_top_k 分别设置两类候选上限。空年报作用域返回明确错误；时点选择后法规为空时保持为空，不放开作用域搜索。

组装器按“年报、法规”轮流取候选，并保持每类来源自身排序。相同 chunk_id 先去重；同一 ID 若出现来源角色冲突或不同原文、版本、定位，则拒绝组装。

每次尝试都对“当前完整上下文 + 新完整证据块”重新计数，不把单块 token 数相加。过长片段记录为省略后继续检查后面的短片段，原文不截断。编号只为实际入选片段生成：年报为 A1、A2……，法规为 R1、R2……；跳过片段不会占用编号。

保存的内容包括：

- 实际 context_text、其 SHA256、完整 source_records 及独立哈希。
- 每条证据的来源角色、原文、文件版本、物理页或正文字符区间、来源登记状态与解析限制。
- 未入选片段及去重、预算省略原因。
- 实际参考计数、分词器身份、两类来源缺失情况和预算诊断。

“有片段因预算被省略”“预算恰好用满”“候选因预算全部被排除”是不同状态。全部排除时任务形成 budget_exhausted；部分来源缺失时仍可形成上下文，但 evidence_status 标为 insufficient。双源候选各有若干条，不保证预算组装后两类来源都仍存在。

## 参考计数及分词器身份

| 档位 | 计数方式 | budget_unit | 身份边界 |
| --- | --- | --- | --- |
| qwen3 | 固定 tokenizer.json，add_special_tokens=false | reference_tokens | 是该模型的参考分词器，不是生成 API 供应商计费分词器 |
| utf8_diagnostic | UTF-8 编码字节长度 | utf8_bytes | 仅诊断预算单位，不能表述为模型 token 或 provider tokens |

qwen3 固定使用 Qwen3-Embedding-0.6B 的 revision：

`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`

固定 tokenizer.json SHA256：

`def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a`

可通过 REFERENCE_TOKENIZER_FILE 改变文件位置；如设置 REFERENCE_TOKENIZER_SHA256，其值必须与上述固定哈希相同。任意自定义分词器不能借用 qwen3 名称。提交时冻结文件哈希和 tokenizers 库版本，Worker 执行时再次核对身份，不静默切换到诊断计数。

API 字段沿用 max_reference_tokens；当 tokenizer_kind=utf8_diagnostic 时，该字段限制的是 UTF-8 字节，响应中的 budget_unit 会明确区分。参考预算上限既不代表实际用量，也不等同于生成服务费用。

## 法规时点选择

默认 temporal_filter=false，不根据报告年度、文件名、发布日期或当前时间推断事项日期。只有显式开启过滤并提供 target_date 及 target_date_role 后，才按登记元数据筛选。

| 字段 | 含义 |
| --- | --- |
| target_date | 明确的 YYYY-MM-DD 日期 |
| target_date_role | event_date 或 evaluation_date |
| effective_from | 登记的生效日期，可未知 |
| effective_to_exclusive | 登记的失效上界，不含该日 |
| interval_end_status | unknown、known_end 或明确声明的 unbounded |
| unknown_temporal | 未知期间按 retain 或 exclude 处理 |

日期字符串格式错误、倒置区间、known_end 缺少结束日期、unbounded 却又填写结束日期，均为元数据错误。只有 JSON null 或缺失值表示未知，空字符串不能冒充未知日期。

已知边界表明事项在有效期之外时直接排除；其余无法判定的行按未知策略保留或排除。没有结束日期不会自动视为无限有效。筛选记录包含逐片段的决定和原因；“登记期间内”不等于法规对该公司事项的适用性已经核实。

开启时点过滤的示例参数：

```json
{
  "temporal_filter": true,
  "target_date": "2024-03-31",
  "target_date_role": "event_date",
  "unknown_temporal": "retain"
}
```

## 数据库升级

在 backend 目录、已正确配置 APP_DATABASE_URL 和显式 DALUN_ENV_FILE 的环境中执行：

```bash
python -m alembic -c alembic.ini upgrade head
```

阶段 2 的 0001_documents 继续引用冻结的 schema_v2.py，避免后来修改“建表函数”而悄悄改变旧迁移含义。阶段 3 的文件 0002_retrieval_contexts.py、revision 0002_contexts 增量添加取消、进度及重试字段，并建立 t_job_attempt 和 t_retrieval_run。

升级保留既有公司、文档、原文件版本、解析、片段、索引及任务数据，不自动补造历史证据或删除旧版本。CLI 的 migrate 也使用同一增量升级逻辑，适用于显式 SQLite 诊断配置。正式库升级前仍须备份；迁移的 downgrade 主动拒绝删除证据，恢复应使用已核验的备份。

## HTTP 使用

接口通过既有 /docs 查看参数。以下 ID 均为格式占位，需替换为环境中真实存在的 ready 索引、任务或运行 ID。

| 方法和路径 | 用途 |
| --- | --- |
| POST /api/v1/retrieval/contexts | 创建上下文任务，202 返回 job_id 和 run_id |
| GET /api/v1/retrieval/contexts | 分页列出运行 |
| GET /api/v1/retrieval/contexts/{run_id} | 查看冻结请求、状态及可回放快照 |
| GET /api/v1/jobs | 分页查看任务，可按状态筛选 |
| GET /api/v1/jobs/{job_id} | 查看状态、进度、尝试次数及结果 |
| POST /api/v1/jobs/{job_id}/cancel | 请求取消 |
| POST /api/v1/jobs/{job_id}/retry | 创建新身份重试，返回202 |

创建请求可使用 Idempotency-Key 请求头。相同幂等键与相同公共参数返回同一任务，冻结身份在首次提交时确定；键与不同请求组合冲突时返回409。

```json
{
  "index_id": "READY_INDEX_ID",
  "query": "审计委员会履职与信息披露义务有哪些依据",
  "stock_code": "603288",
  "report_year": 2023,
  "annual_top_k": 5,
  "regulation_top_k": 5,
  "max_reference_tokens": 6000,
  "tokenizer_kind": "qwen3",
  "temporal_filter": false,
  "target_date": null,
  "target_date_role": null,
  "unknown_temporal": "retain",
  "max_runtime_seconds": 600
}
```

创建任务不会在 HTTP 请求中执行 CUDA 检索。另行启动 Worker，再轮询任务与 run_id。响应封装沿用 code/message/data；成功入队不表示上下文已经生成。

## CLI 使用

沿用[阶段 2 环境与启动说明](../stage2/README.md)。在 backend 目录执行：

```bash
python -m app.documents.cli migrate
python -m app.documents.cli context READY_INDEX_ID 审计委员会履职依据 --stock-code 603288 --year 2023 --annual-top-k 5 --regulation-top-k 5 --max-reference-tokens 6000 --tokenizer-kind qwen3 --key context-example-1
python -m app.documents.cli worker --once
python -m app.documents.cli job JOB_ID
python -m app.documents.cli show-context RUN_ID
python -m app.documents.cli contexts
python -m app.documents.cli jobs
python -m app.documents.cli cancel JOB_ID
python -m app.documents.cli retry JOB_ID --key retry-example-1
```

诊断环境将 --tokenizer-kind 改为 utf8_diagnostic；这不会把所选索引档变成 CUDA，也不会把字节计数升级为模型 token。时点筛选可附加 --temporal-filter、--target-date、--target-date-role 和 --unknown-temporal。

## 取消、重试与恢复

排队任务可立即取消。运行中任务记录 cancel_requested，由检查点协作停止；模型子进程受取消检查和运行时 deadline 控制。原文解析等工作在可检查点响应取消，不承诺任何操作都能即时中断。

Worker 的 owner、租约有效期、运行状态和取消标记共同控制发布。关键结果发布事务先对任务行做条件更新并取得写入资格，再提交相应业务结果。失去租约的旧进程不得提交成功或失败状态，已发布就绪索引也不得因旧进程异常而被改坏。

t_job_attempt 记录每次实际执行的 owner、编号、起止时间、状态和错误。过期回收只结束对应旧 owner 和旧编号的尝试，不会把接管者的新 attempt 标记为 abandoned。有限自动恢复仍受尝试次数限制。

失败、取消或预算耗尽的手动 retry 创建新 job；上下文会创建新 run_id，并保留父任务与父运行关系；索引重试创建新索引身份。原任务、旧快照和错误记录保留，不把失败运行改写成成功运行。max_runtime_seconds 是每次执行的运行时预算；手动新任务与恢复尝试的日志分别保留。

若 Worker 在上下文快照已持久化、任务尚未完成时中断，恢复尝试先核对原快照、请求和分词器身份，复用已发布内容，不重复检索或覆盖其哈希。终态读取同时核对 run/job 身份、任务提交的快照哈希、原始 attempt 和来源记录哈希。取消或失败任务不对外显示残留上下文为成功结果。

## 实际验证

[真实文档验证记录](real-validation.json) 使用阶段 2 的真实年报与法规索引、真实 CUDA 检索模型及固定 Qwen 参考分词器，数据库为 SQLite 诊断档。本轮不调用生成 API。

年报与法规分别取5条候选，10个完整片段全部入选，实际为 **4898 参考 tokens，不超过6000上限**。10个 A/R 来源位置逐条核对通过，保存后回放的快照哈希保持一致。定位校验与回放一致性不代表语义支持或法规适用已经认证。

冻结候选上的预算回放没有重新调用生成模型：

| 参考预算 | 实际参考 tokens | 入选片段 | 省略片段 | 观察 |
| --- | --- | --- | --- | --- |
| 1 | 0 | 0 | 10 | 所有候选因预算排除 |
| 1000 | 869 | 2 | 8 | 仅年报入选，法规证据不足 |
| 2000 | 1948 | 4 | 6 | 年报3条、法规1条 |
| 6000 | 4898 | 10 | 0 | 两类来源各5条 |

完整片段策略可能留下未用预算，也可能使某类来源未能入选，因此不能将候选配额等同于最终双源覆盖。

本轮 CI 166 项测试通过；迁移从阶段 2 版本开始，保留种子公司后升级到阶段 3，HTTP 与 PostgreSQL 生命周期及前端生产构建通过。精确记录见[测试摘要](test-summary.json)及[已验证代码检查](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40291767626)。完整服务器部署与实际浏览器交互仍需系统级验收。

公开仓库只保存必要代码、文件/索引/上下文哈希及验证摘要；原始语料、完整私有上下文快照和运行环境配置不上传。它们保留在配置的运行目录中，供本地核查和恢复。

## 领取策略细化

当前使用事务与条件 UPDATE（CAS）领取任务，兼容 PostgreSQL 和 SQLite；未启用最初设计中的 SKIP LOCKED。租约、owner、执行编号和原子发布共同防止旧进程覆盖新结果。并发回收及同键竞争有受控屏障测试覆盖；本阶段不宣称大规模多 Worker 吞吐性能。
