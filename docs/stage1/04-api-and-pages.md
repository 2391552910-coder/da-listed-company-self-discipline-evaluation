# 接口与页面设计

状态：待实现契约。沿用 /api/v1；现有 /reports 是年报，/reports-export 是报告导出，新增 /documents 避免进一步混淆。

## 接口清单

| 方法与路径 | 用途 | 当前实现处理 |
| --- | --- | --- |
| GET/POST /companies | 公司分页与录入 | 保留，补 total 及校验 |
| GET /dashboard?year=2024 | 文档、任务、覆盖统计，指数可空 | 新增，替换固定图表 |
| POST /documents/upload | multipart 上传年报/法规及身份字段 | 新增；旧 /reports/upload 过渡代理 |
| GET /documents | 按来源、公司、年度、状态筛选 | 新增 |
| GET /documents/{id}/versions | 文档版本历史 | 新增 |
| POST /document-versions/{id}/parse | 202 返回解析 job_id | 新增；旧同步 parse 逐步停用 |
| GET /document-versions/{id}/chunks | 分页查看片段、定位及质量状态 | 新增 |
| GET /document-versions/{id}/file | 原文件流，支持 PDF 阅读与下载 | 新增；以受控 storage_key 解析 |
| GET /chunks/{id}/source | 原文、分段位置、哈希及核实状态 | 新增 |
| POST /indexes | 创建构建任务，返回 job_id | 新增 |
| GET /indexes | ready/staging/failed 及模型身份 | 新增 |
| POST /retrieval/search | 公司年度限定的双源调试检索 | 新增；旧 /rag/search 保留 legacy 标识 |
| GET /question-sets | 版本化四维评价问题模板 | 新增 |
| POST /evaluations | 创建单题或四维评价，返回 run_id/job_id | 新增 |
| GET /evaluations | 按公司年度/模式/状态分页 | 新增 |
| GET /evaluations/{run_id} | 运行、各题、四维摘要和限制 | 新增；旧 /{stock_code}/{year} 标记弃用 |
| GET /evaluations/{run_id}/answers/{answer_id}/evidence | 该题最终上下文、论断与引用 | 新增 |
| GET /evaluations/{run_id}/trace | 配置、候选、各轮上下文、工具和调用摘要 | 新增，鉴权信息始终过滤 |
| GET /jobs/{id} | 持久化进度、子任务、错误及预算 | 新增 |
| POST /jobs/{id}/cancel | 请求协作取消，不承诺中断已发出的 API | 新增 |
| POST /evaluations/{run_id}/retry | 对失败单元创建新 run/job，链接 parent_run_id | 新增，不覆盖原始运行 |
| POST /evaluations/{run_id}/exports | 创建 HTML/PDF 导出任务 | 新增 |
| GET /exports/{id}/file | 下载已完成制品 | 新增 |
| GET /health/live、/health/ready | 进程存活与依赖就绪 | 增强；旧 /health 仅存活 |
| POST /index-results/import | 第三章外部结果导入 | 预留，资料到位后实现 |

POST /evaluations/run 旧同步接口在迁移期明确返回 legacy 流程标识，前端改用新接口后停止创建旧评价。静态路由、单段 UUID run_id 和旧双段代码/年度路由需做路由匹配测试。

## 创建评价示例

请求头 Idempotency-Key 用于相同任务提交去重，重复键与不同请求体组合返回409。示例 UUID 仅表示格式，真实请求须引用已存在实体。

```json
{
  "company_id": 1,
  "report_year": 2024,
  "index_version_id": "00000000-0000-4000-8000-000000000001",
  "mode": "dual_fixed",
  "question_set_version": "four-dim-v1",
  "questions": [
    {
      "key": "D2-01",
      "dimension": "D2",
      "text": "审计委员会履职披露有哪些依据与限制？",
      "declared_target_date": null,
      "target_date_role": null
    }
  ],
  "retrieval": {
    "annual_top_k": 5,
    "regulation_top_k": 5,
    "max_reference_tokens": 6000,
    "temporal_filter": false,
    "unknown_temporal": "retain"
  },
  "budget": {
    "max_model_calls": 8,
    "max_supplemental_calls": 0,
    "max_observed_tokens": 80000,
    "request_timeout_seconds": 600
  },
  "execution_profile": "local_cuda"
}
```

服务端校验 mode、范围、日期和索引身份，冻结完整配置。若传自定义 questions，它们构成当前运行的问题快照，并记录 base_question_set_version，不得冒称与完整四维模板完全一致。max_model_calls 覆盖单题生成、核验、修订及汇总的所有请求；不足以执行计划则在排队前拒绝。以上预算为契约示例，不是历史实验实测配置。

202 响应：

```json
{
  "code": 0,
  "message": "评价任务已创建",
  "data": {
    "run_id": "00000000-0000-4000-8000-000000000002",
    "job_id": "00000000-0000-4000-8000-000000000003",
    "status": "queued"
  },
  "request_id": "request-example"
}
```

## 结果及错误语义

分页统一 data.items/total/page/page_size。业务结果使用显式 Pydantic DTO，不把 ORM 对象或绝对文件路径直接输出。

- 任务状态：queued → running → succeeded / partial / failed / cancelled / budget_exhausted。指数未接入不使文本评价失败。
- 文档状态：uploaded、parsing、parsed、partial、failed；索引状态单独管理。
- 单题执行状态与证据状态分开：status=succeeded 仍可 evidence_status=insufficient。
- 支持状态：模型判断 supported/partial/unsupported/conflicted/unknown；另有 human_review_status=not_reviewed/confirmed/rejected。
- 400 为业务前置条件不满足，404 为资源不存在，409 为版本或幂等冲突，422 为参数校验失败，503 为所选模型/存储档不可用。
- 典型错误码：DOCUMENT_NOT_READY、INDEX_NOT_READY、ANNUAL_SCOPE_EMPTY、MODEL_PROFILE_UNAVAILABLE、SOURCE_UNAVAILABLE、BUDGET_TOO_SMALL。
- 缺少法规候选可返回证据不足结果，不能放开作用域检索；完全没有指定公司年度年报时拒绝评价。
- 导出只读取指定运行；partial 导出附明确缺项，failed/尚无正文则不生成正式报告。
- 下载成功返回正确 MIME 与 UTF-8 文件名；前端先判断 HTTP 状态，不能把 JSON 错误保存成 PDF。

## 页面与路由

| 页面/目标路由 | 主要区域 | 空态和异常 |
| --- | --- | --- |
| 总览 / | 公司年度筛选、文档就绪数、任务状态、四维证据覆盖；指数接入后再显示分布 | 无指数显示“指数结果未接入”，不放演示分数 |
| 公司 /companies | 搜索、分页、录入、公司年度资料入口 | 无年报时引导导入 |
| 文档中心 /documents | 年报/法规切换、上传、版本、解析状态、片段与索引状态 | OCR待核实、选页导入、解析部分失败分别展示 |
| 评价工作台 /evaluations/new | 公司年度、问题模板/单题、固定或Agent模式、模型档、预算 | 前置检查失败给具体原因 |
| 任务 /tasks | 排队/运行/失败列表、进度、预算、取消及重试 | 刷新可恢复；重试新建运行 |
| 评价详情 /evaluations/:id | 总体摘要、四维题目、结论限制、正文引用、运行版本 | 各维无证据和技术失败分别显示 |
| 证据 /evidence | 左侧原文件或文本帧，右侧片段、页码、版本、引用论断 | 无原件显示 source_unavailable 与历史快照；无PDF位置时用真实字符区间 |
| 实验 /experiments | 按数据/问题/模式版本筛选对照、失败比例、耗时与调用用量 | 人工/自动评分分栏，历史导入与新实跑分组 |
| 指数 /index | 第三章结果接入位置 | 当前未接入；不暴露旧随机计算为正式方法 |
| 报告 /reports-export | 指定运行、预览、HTML/PDF、导出历史 | 无指数仍可导出完整文本评价与证据附录 |

实验页先提供可比运行的筛选与表格，质量评测需有独立评分导入；不因两条运行都成功就自动给出质量提升结论。旧 /evaluation、/import、/report 可在新页面上线后重定向，避免同时维护两套业务路径。

界面采用清晰中文、白底、克制配色和宽度自适应布局。证据显示来源角色、定位状态、模型判断与人工复核状态，避免单个“可信”绿标概括全部含义。
