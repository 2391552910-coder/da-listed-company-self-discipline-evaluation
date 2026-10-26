# 项目设计文档

更新日期：2026-10-26（北京时间）。阶段6/7代码已交付并通过Linux/Windows、4条浏览器、Compose及PG冷备恢复检查。真实模型实验、阶段4真实生成及正式服务器上线仍需独立验收。不得将fixture测试响应作为论文模型实证。

| 文档 | 用途 |
| --- | --- |
| [01 需求范围与架构](stage1/01-scope-and-architecture.md) | 已确认需求、业务边界、架构及部署决策 |
| [02 代码审计](stage1/02-baseline-audit.md) | 源码证据、优先级、改造位置及验证范围 |
| [03 数据设计](stage1/03-data-design.md) | 实体、关键字段、约束、版本与迁移 |
| [04 接口与页面](stage1/04-api-and-pages.md) | 新旧接口、请求与响应、页面及异常状态 |
| [05 实施与验收](stage1/05-implementation-and-acceptance.md) | 可执行工作包、验收用例、同步要求 |
| [06 研究复用](stage1/06-research-reuse.md) | 论文来源、代码复用、参数与迁移验证 |
| [审计脚本](stage1/audit_baseline.py) | 无外部依赖的基线检查 |
| [审计结果](stage1/baseline-audit.json) | 本轮实际执行结果，非系统验收结果 |

阶段 1 的完成含义：已完成现有实现核查，并形成后续开发所需的需求边界、模块改造、数据库、接口、页面、研究复用与验收方案。文档中的“拟新增”“目标”“验收”均为未来实现要求。

重要内容同步规则：每个有实质成果的开发工作包同步代码、必要设计变更和验证摘要；提交说明区分“设计完成”与“功能已验证”。保留失败和限制。大文件与运行数据通过哈希清单关联，不能把样例、模拟输出或历史论文结果包装成新系统实测。

## 阶段 2 实现与验证

[使用说明、设计细化和验证边界](stage2/README.md) · [真实CUDA验证](stage2/real-validation.json) · [测试摘要](stage2/test-summary.json)

## 阶段 3 上下文与任务

[功能、API、迁移与运行说明](stage3/README.md) · [真实 CUDA 与预算回放](stage3/real-validation.json) · [测试摘要](stage3/test-summary.json)

## 阶段4 评价与论断证据

[功能、调用预算、恢复与验收边界](stage4/README.md) · [真实资料验证记录](stage4/real-validation.json) · [最终测试摘要](stage4/test-summary.json)

## 阶段5 工作台与报告

[页面流程、冻结导出与浏览器验收](stage5/README.md) · [最终测试摘要](stage5/test-summary.json)

## 阶段6 实验与Agent扩展

[受限Agent、实验协议、评分与历史隔离](stage6/README.md) · [最终测试摘要](stage6/test-summary.json)

## 阶段7 部署与交付

[双环境启动、持久化、备份恢复与交付检查](stage7/README.md) · [最终测试摘要](stage7/test-summary.json) · [PG恢复验证](stage7/postgres-backup-validation.json)


- [系统设计完成度核查与功能补齐](completion/README.md)：总览、公司分页、文档筛选/片段分页、评价方法筛选；包含剩余研究验收边界。
