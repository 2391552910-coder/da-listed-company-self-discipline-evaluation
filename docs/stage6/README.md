# 阶段6：实验与受限Agent扩展

日期：2026-10-26（北京时间）。本轮交付代码与可执行检查，不把合成测试响应或未运行实验当作论文实证。最终结果见测试摘要，部署入口见[阶段7](../stage7/README.md)。

## 固定流程与Agent

`Evaluations.enqueue` 新增 `mode=dual_fixed|dual_agent` 和可选 `agent`。固定模式保留阶段4冻结请求的原有字段结构，旧结果和报告仍可读。Agent为本系统新增协议 `bounded-dual-agent-v1`，不冒称与论文历史Agent配置完全相同。

Agent每题执行有限轮规划，规划仅能选择同一冻结索引内的年报、法规或两类补检索，不能改变公司、年度、事项日期或访问任意工具。完整片段预算组装后生成草稿，进行一次模型自检；必要时最多修订一次。草稿以后上下文与A/R编号固定，修订后明确“未再次核验”。模型自检不表示独立人工复核或语义事实确认。

```json
{
  "mode": "dual_agent",
  "agent": {"max_rounds": 1, "max_supplemental_calls": 4, "allow_revision": true},
  "budget": {"max_model_calls": 16, "max_observed_tokens": 80000, "max_output_tokens": 8000, "max_runtime_seconds": 1800, "request_timeout_seconds": 600}
}
```

调用预算至少覆盖题数 ×（规划轮上限 + 草稿 + 自检 + 可选修订）。默认四题需预留16次，实际规划停止或不修订时可少用；不是自动用满额度。补检索次数为全运行共享上限，允许0至12，轮数1至3；所有请求同时受阶段4调用数、已观察token阈值和累计运行时长约束。

每步保存提示词、规划或检索参数、候选和上下文、使用预算、调用purpose/step_key、响应及错误。先写调用或工具reservation，再执行；已保存响应及检索结果恢复直接复用。调用结果未知或补检索已预留但未保存结果时保守终止，不自动重发或静默退回固定流程。

## 实验协议与对照

`app.experiments` 冻结实验名称、案例、问题、索引清单、tokenizer、模型、提示词、Agent协议及预算。最多8案例、16个对照单元，总模型调用预留上限128；创建实验只排队，不在API内运行模型。每个子任务采用确定性幂等键，派发中断可恢复；取消会找回已排队但未绑定的子任务，既有运行的关联不改变其所有权。

比较按公司年度、索引、问题、检索、tokenizer、模型、提示词和预算分组。模式及Agent配置作为明确的受控差异。运行成功、耗时、调用数和已报告用量仅为运行指标，不能当质量分数或证明Agent提升。缺少匹配组、未执行、技术失败、证据不足和未评分分别显示。

问题由实验协议显式冻结，子运行标记自定义问题快照并保留基础模板版本；不把它冒称为未修改的默认四维模板。

## 评分与历史

量表 `evidence-quality-v1` 含忠实性、覆盖度、专业性、引用质量，每项1至5或null，至少一项有值；不以0代替失败。评分只绑定已成功回答的实际快照SHA256，来源 `manual` 与 `automatic`、评审者、方法版本、备注和更正链分别保留。评分只能追加，更正引用旧记录，不覆盖旧证据；自动评分记录是外部评审输入，不声称系统已自动完成独立质量评审。

历史导入独立标记 `historical_import`，每批声明来源身份与标签，最多200条；失败或未运行记录不能带非空质量评分。历史答案不会变成新评价运行，不进入新实跑指标。关联既有运行标记 `existing_run`。

JSON/CSV导出对当前实验形成内容哈希快照，保留协议、运行、评分和历史分组；CSV防公式注入，未知用量显式保留，不自动计算混合质量均值。

## API与CLI

| 路径 | 用途 |
| --- | --- |
| POST/GET /api/v1/experiments | 创建与分页列表 |
| GET /api/v1/experiments/rubrics | 量表及范围 |
| GET /api/v1/experiments/{id} | 协议、进度、实际运行、对照和评分 |
| POST .../{id}/dispatch、cancel | 恢复未绑定单元、取消与补偿 |
| POST .../{id}/ratings | 追加人工或自动评分 |
| POST .../{id}/runs | 关联已有运行 |
| POST .../{id}/history | 独立历史导入 |
| GET .../{id}/exports?format=json或csv | 带哈希的快照导出 |

创建、评分、历史导入支持Idempotency-Key。HTTP DTO使用严格整数和布尔值，拒绝额外字段。新增Alembic `0004_experiments_agent`（业务revision5），增量增加实验表、模型调用步骤字段与Worker健康表，不改旧迁移。

CLI新增 `create-experiment request.json`、`experiments`、`show-experiment ID`、`dispatch-experiment ID`、`cancel-experiment ID`、`rate-experiment ID request.json`、`import-experiment-history ID request.json`、`attach-experiment-run ID RUN_ID` 与 `export-experiment ID --format json --output NEW_FILE`。导出拒绝覆盖既有文件。

## 验证边界

- Agent协议和执行恢复测试、实验取消崩溃窗口、评分绑定、历史隔离、比较分组、预算和并发幂等均有测试。
- 新增浏览器用例使用实际UI/API/独立Worker、受限补检索与loopback HTTP provider；文档与模型响应明确为合成fixture。
- 本轮没有真实外部模型调用，也没有研究数据上的固定/Agent质量对比结论。真实API验收仍沿用阶段4阻塞记录，完整研究实验须另行执行并导入独立评分。
