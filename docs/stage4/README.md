# 阶段4：可追溯模型评价

日期：2026-10-26（北京时间）。业务代码已实现，单题与四维的真实模型生成验收受当前网络环境阻塞。不要将下述测试响应当作论文模型实证。

## 实现

- `app.evaluation.service.Evaluations`：单题、自定义问题快照及版本化四维基础模板，年报限定公司年度；每题独立检索、完整片段预算与冻结提示词。模板为本系统编制，未冒称历史实验问题集。
- D1信息披露、D2董事会治理、D3内部控制与合规、D4第三方声誉；D4仅年报/法规时强制披露独立第三方资料缺口。第三章指数及四维分数均为null，不用文本回答填充指数。
- 每题一次真实OpenAI兼容HTTP请求；服务器配置 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`，支持显式JSON模式、可省略temperature。没有生产mock回退，不加载研究项目的隐式.env。API和Worker使用同一配置。
- 严格JSON输出包含answer、evidence_status、limitations、claims；论断含模型support_status和逐字引用。未知编号、正文编号与claims不一致、错误quote、无法验证原件位置、重复JSON字段、截断输出均显式失败，保留原始输出与错误诊断。
- 引用进入上下文、模型引用、逐字匹配、原件位置核对、模型支持判断、人工复核状态分别记录。机械校验不能证明语义支持、法规适用或结论质量。
- 四维汇总按实际题目状态确定性归并，不再发一次模型请求，不生成资料外综合结论。保留缺失维度、失败、证据不足、自定义题集标识。
- 每次外部调用先写持久账本。完成响应、逐题结果、整次运行分别冻结并校验SHA256；恢复复用已保存响应/结果，in_flight变为uncertain，不自动重发。显式重试创建新run并链接原运行，可能产生新消费。
- 运行时长从首次执行起累计，包括停机；调用数和已观察provider token持久累计。参考片段token与完整prompt/计费token不同。max_observed_tokens为已报告用量停止阈值，单次调用可能越过；用量未知立即停止后续调用。
- 取消关闭本地HTTP连接并禁止发布迟到结果；外部服务可能已经消费，不承诺零消费或外部请求恰好一次。

## 数据库与接口

Alembic `0003_evaluations` 在0002基础上新增评价运行、单题、模型调用及报告表；不重写旧迁移，不伪造历史调用。数据库就绪要求业务revision4。

| 接口 | 用途 |
| --- | --- |
| GET /api/v1/model-profiles | 安全配置状态；available只代表配置完整，非网络或服务可用性探测 |
| GET /api/v1/question-sets | 四维模板版本及资料边界 |
| POST /api/v1/evaluations | 202排队，支持Idempotency-Key |
| GET /api/v1/evaluations | 公司、年度、状态分页 |
| GET /api/v1/evaluations/{id} | 冻结运行、单题、调用与四维汇总 |
| GET /api/v1/evaluations/{id}/trace | 完整脱敏运行追踪 |
| GET /api/v1/evaluations/{id}/answers/{answer_id}/evidence | 单题上下文、论断、引用和定位 |
| POST /api/v1/evaluations/{id}/retry | 失败、取消、预算终态新建运行；不覆盖原件 |
| GET/POST /api/v1/jobs/... | 与阶段3共用持久任务、进度、取消与重试 |

实际请求用stock_code与index_id，与阶段1待实现示例中的company_id/index_version_id不同，以OpenAPI和新工作台为准。默认固定双源模式；Agent、人工质量评分、第三章导入尚未实施。

## 运行

安装 `backend/requirements-web.txt`；GPU检索环境沿用阶段2。配置文件须显式指定，密钥不得提交GitHub。先迁移，再分别启动API与Worker：

```bash
python -m alembic -c alembic.ini upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.documents.cli worker
```

可用 `python -m app.documents.cli evaluate request.json` 从JSON请求排队，`show-evaluation RUN_ID` 查看。compose.stage2.yml已向API/Worker注入生成配置；默认容器检索仍为显式BM25诊断档。

## 验证与待验收

- 本地完整套件283 passed、4 skipped（FastAPI与PostgreSQL在本地不可用，交CI补验）；最终CI为288 passed，PostgreSQL增量迁移、Vue构建及2条Chromium闭环均通过。其中阶段4核心88项、报告21项均包含在完整套件内，不重复相加。测试模型响应均标记fixture，见[最终测试摘要](test-summary.json)。
- 真实年报/法规复用阶段3可校验CUDA索引，单题检索10条来源、4898/6000参考token、10条原件位置核对通过。
- 真实调用返回MODEL_TRANSPORT_FAILED；网络到api.kimi.com:443无法连接。账本保留uncertain和usage unknown，未收到真实模型回答，未执行后续四维真实生成，也未生成真实模型报告。详细记录：[real-validation.json](real-validation.json)。
- 网络可用后执行 `backend/scripts/smoke_evaluations.py --baseline-state <阶段3状态目录> --work-dir <全新目录> --model-python <CUDA解释器> --llm-env-file <显式配置文件> --output <脱敏摘要.json>`。脚本先单题再四维，失败即停；成功后生成并重新校验HTML/PDF。
- 真实生成成功、独立人工语义核验、Windows/服务器部署全闭环仍需后续验收。未通过前不宣称阶段4完整验收通过。
