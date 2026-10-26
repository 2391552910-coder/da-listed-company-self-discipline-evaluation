# 项目设计文档

更新日期：2026-10-26（北京时间）。阶段 1 和阶段 2 已完成；生成评价等后续阶段尚未实施。

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
