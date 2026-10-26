# 阶段 2 双源文档与研究索引

日期：2026-10-26。当前实现已具备文档版本、定位、持久任务、研究检索适配、CLI 与 HTTP 入口；验证记录将在本阶段结束时补齐。本目录所有“待验证”均不代表已完成系统验收。

## 已实现范围

- 年报 PDF 与法规 PDF/TXT/DOCX 导入，文件哈希、版本和法规元数据登记。
- 年报按公司年度归组；新文件新增版本，不删除旧文件、解析或索引。
- 原文件完整性检查；PDF 使用 PyMuPDF 保留物理页文本帧、字符区间与哈希。
- 年报开篇公司/年度匹配及摘要/半年报拒绝；这不是全文真实性认证。
- 无文字页报告需OCR，选页解析保留范围警示；不伪造OCR文字、页码或法规有效日期。
- 每次重解析新增不可变解析批次；片段定位可回到当时的文本帧。
- 独立 Worker 处理持久化解析与索引任务，支持幂等提交、租约、心跳和有限恢复。
- Qwen3 Embedding/Reranker 0.6B 的 CUDA 档，以及明确标识的 BM25 离线诊断档。
- 双源候选按公司、年度和来源类型限定，空作用域不全库回退。
- 保存索引制品、模型身份、片段映射与候选阶段跟踪；查询前后验证文件与索引。
- 文档中心、索引选择、任务状态、检索与原文位置查看页面。
- PostgreSQL/Alembic 配置、Windows/Linux 启动脚本、容器诊断配置和 CI。

## 运行环境

正式 Web 环境使用 Python 3.12、PostgreSQL、FastAPI。模型放在独立 CUDA 环境，通过 MODEL_PYTHON 调用；Web 不需安装 torch、Milvus 或 MinerU。

在 backend 中：

```bash
python -m venv .venv
# 激活虚拟环境后
python -m pip install -r requirements-web.txt
```

复制 backend/.env.example 为自己的配置文件，并设置 DALUN_ENV_FILE 为其绝对路径。APP_DATA_ROOT 建议使用绝对路径；MODEL_PYTHON 指向装好 CUDA PyTorch 与 requirements-models.txt 的解释器。仅显式指定的配置文件会被读取，系统不会自动读取小论文工程的 .env。

依赖范围写在 requirements 文件中；模型档固定模型 revision、float16、batch=1，查询指令与原型一致。当前本机档 max_seq_length=2048，区别于历史实验32768配置，不能冒称为同一历史试验。模型缓存需提前准备；模型子进程开启离线模式，不会自动下载模型或静默回退CPU。

### Windows

在仓库根目录分别执行：

```powershell
$env:DALUN_ENV_FILE = '你的绝对配置路径'
.\scripts\start-stage2.ps1 -Mode migrate -Python '你的Web环境Python路径'
.\scripts\start-stage2.ps1 -Mode api -Python '你的Web环境Python路径'
# 另开进程
.\scripts\start-stage2.ps1 -Mode worker -Python '你的Web环境Python路径'
```

### Linux 服务器

```bash
export DALUN_ENV_FILE=/absolute/path/to/dalun.env
export DALUN_PYTHON=/absolute/path/to/web-venv/bin/python
bash scripts/start-stage2.sh migrate
bash scripts/start-stage2.sh api
# 另一服务进程
bash scripts/start-stage2.sh worker
```

前端按 frontend/package.json 安装依赖后启动 Vite，访问 /documents。生产入口使用 frontend/nginx.conf 的 /api 代理和 SPA 回退。

### 容器诊断档

```bash
export POSTGRES_PASSWORD=replace_with_local_password
docker compose -f compose.stage2.yml up --build
```

容器配置提供 PostgreSQL、迁移、API、Worker、前端及持久卷。此镜像不包含 CUDA 模型，只能选择 BM25 诊断档；Qwen CUDA 档须按前述原生 API/Worker + 隔离模型环境运行。不要用旧 docker-compose.yml 代表新阶段部署。

## CLI 与 HTTP

CLI 与 HTTP 使用相同领域服务。backend 目录运行：

```bash
python -m app.documents.cli company 603288 海天味业
python -m app.documents.cli upload annual.pdf --corpus annual_report --title 年报 --company-id 1 --year 2023
python -m app.documents.cli upload regulation.pdf --corpus regulation --title 监管规则
python -m app.documents.cli parse VERSION_ID
python -m app.documents.cli worker --once
python -m app.documents.cli index PARSE_ID_1 PARSE_ID_2 --profile qwen_cuda
python -m app.documents.cli worker --once
python -m app.documents.cli search INDEX_ID 审计委员会履职依据 --stock-code 603288 --year 2023
python -m app.documents.cli source CHUNK_ID
```

API 文档为 /docs；解析与索引创建返回202及持久任务ID，前端轮询 /api/v1/jobs/{id}。原文件与定位接口分别为 /api/v1/document-versions/{id}/file 和 /api/v1/chunks/{id}/source。

## SQLite 离线诊断

仅为缺少部署依赖的开发环境提供，不替代 PostgreSQL 验收。设置：

```text
APP_DATABASE_URL=sqlite:///absolute/path/to/diagnostic.sqlite
APP_DATA_ROOT=/absolute/path/to/diagnostic-assets
```

运行 python -m app.documents.cli migrate，再选择 bm25_diagnostic。CLI 核心诊断依赖标准库；PDF仍需 PyMuPDF。HTTP 入口始终需要 requirements-web.txt。

## 测试与复现

backend 中运行：

```bash
python -m pytest tests -q
```

test_api.py 缺少 FastAPI/multipart 时会显式跳过；test_postgres.py 仅在 TEST_POSTGRES_URL 指向一次性测试库时运行。CI 使用独立 PostgreSQL 服务并安装真实依赖，不访问本地源文件或配置。

scripts/smoke_real_documents.py 接受真实年报、法规、公司年度和模型Python路径。它以完整PDF导入、解析、构建索引、双源检索、逐条位置核对为流程，结果只保存哈希、数量及验证结论。可通过 --reference-root 对照研究原版排序实现；该对照共享已加载的冻结编码器，验证适配与排序一致性，不代表独立模型准确率评测。

## 设计细化与当前边界

1. 阶段2只生成单页或单文本帧片段，暂不合并跨页条款；因此每块有一个精确区间。跨页组合和完整条款构造留待后续扩展，不能声称当前已实现。
2. 新索引格式 dalun-source-index-v1 使用研究检索算法，保存业务UUID到片段映射；不直接把历史 xiao-traceable-dual-index 当成新格式加载。
3. 文档子系统通过独立 repository 边界访问数据库：PostgreSQL 使用 SQLAlchemy 连接管理与DB-API事务，SQLite使用标准库；领域逻辑一致。旧ORM表与原型源码保留，尚未做历史来源回填。
4. Alembic迁移是增量建表，不删除既有公司或证据；downgrade主动拒绝破坏来源。正式迁移前仍须备份与核查已有库表结构。
5. 当前只有文档和索引任务；取消、评价任务、LLM调用账本、四维评价和报告生成属于后续阶段。
6. 页面上的“定位校验通过”仅表示原文件哈希与文本帧区间一致，不表示语义支持、人工复核或法规适用已认证。
7. 当前没有用户权限与公网防护，默认本机/受控网络启动，不将其宣传为可直接公网运行的监管业务平台。
