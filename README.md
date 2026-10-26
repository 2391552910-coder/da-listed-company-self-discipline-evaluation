# 基于大模型的上市公司主体自律评价系统

硕士论文《基于大模型的上市公司主体自律评价系统的设计与实现》配套工程。

**当前状态：阶段1至3已验收；阶段4评价代码已实现，阶段5工作台/报告已通过浏览器测试资料闭环验收。** 288项后端测试、PostgreSQL迁移、前端构建及2条Chromium流程通过。真实CUDA检索与来源定位通过；当前环境无法连接生成服务，真实单题/四维生成验收尚未通过。功能、验证及限制见[阶段4](docs/stage4/README.md)与[阶段5](docs/stage5/README.md)。第三章指数仍待资料接入。

## 设计入口

- [文档导航与阶段状态](docs/README.md)
- [需求范围与架构决策](docs/stage1/01-scope-and-architecture.md)
- [现有代码审计与改造清单](docs/stage1/02-baseline-audit.md)
- [数据库与迁移设计](docs/stage1/03-data-design.md)
- [接口与页面设计](docs/stage1/04-api-and-pages.md)
- [开发顺序与验收标准](docs/stage1/05-implementation-and-acceptance.md)
- [小论文方法与代码复用映射](docs/stage1/06-research-reuse.md)

## 已确认的项目边界

- 第三章：主体自律指数，用户稍后提供已有数据和结果；当前仅设计接入位置。
- 第四章：年报与法规双源 RAG，固定双源流程为主，工具 Agent 为可选扩展。
- 第五章：完整 Web 系统设计、实现及测试，服务论文答辩。
- 复用小论文真实语料、检索模型和本地接口配置；兼顾 Windows 与服务器运行。
- 重要需求、设计、代码、测试摘要及阶段结果持续提交 GitHub。真实语料、模型权重、密钥及原始调用记录默认留在运行环境，通过脱敏清单与哈希记录复现关系。

## 技术路线

| 部分 | 设计基线 |
| --- | --- |
| 前端 | Vue3、Element Plus、ECharts、Vite |
| 后端 | FastAPI、SQLAlchemy、独立任务 Worker |
| 业务存储 | PostgreSQL；增加版本化迁移 |
| 文档与索引 | 原文件和定位信息不可变；先适配研究原型的可校验索引制品 |
| 检索 | 年报与法规独立限定作用域，BM25 + 稠密召回，可配置重排 |
| 模型 | 复用 Qwen3-Embedding-0.6B / Qwen3-Reranker-0.6B；生成 API 可配置 |
| 向量存储扩展 | 保留 Milvus 适配方向，须通过同一作用域与模型身份契约验证 |

旧原型源码保留，app.main 启用文档、检索、持久评价、证据与冻结报告。BGE-M3 + Milvus、旧同步评价及第三章计算路由暂未启用。索引评分与大模型文本评价分开表示；第三章结果未接入时不得填充虚构指数或等级。

## 目录

```text
backend/       文档、检索、持久评价任务及HTML/PDF报告
frontend/      文档中心、评价/任务工作台、证据与报告
docker-compose.yml  现有容器配置，存在待修复项
docs/stage1/   已完成的需求、审计、数据、接口及实施设计
```

## 阶段 1 离线核查

在仓库根目录使用 Python 3.10+：

```bash
python docs/stage1/audit_baseline.py --output docs/stage1/baseline-audit.json
```

该命令只解析源码，并隔离执行两个方法以复现已确认缺陷；不启动数据库、不加载模型、不调用外部 API。历史基线为 `cdd17fdd95bd01e658f6931007d62c5b352087a5`。业务修复后，缺陷复现结果允许变化，不应将此审计脚本作为要求缺陷继续存在的回归测试。

阶段 2 的启动、诊断、依赖和已验证范围见 [阶段 2 使用说明](docs/stage2/README.md)。
