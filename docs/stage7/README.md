# 阶段7：部署与交付代码

日期：2026-10-26（北京时间）。本阶段交付部署配置、启动脚本、健康/配置检查、冷备份恢复、交付清单及部署验收脚本。代码与fixture验收不能替代实际Windows/服务器环境和真实研究API验收；后者的结果应另行记录。

## 正式入口

`compose.yml` 是正式容器入口：PostgreSQL16 → 增量迁移 → API/独立Worker → Nginx。数据库和原件/索引/评价/报告共用持久卷，默认仅 `127.0.0.1:8080` 暴露同源前端；API和数据库不直接对外发布端口。`compose.server.yml` 增加资源约束，适用于受控服务器。`docker-compose.yml` 兼容入口引用同一配置，需要Docker Compose2.20.3及以上；旧Milvus原型不再默认启动，历史卷不会被脚本删除或自动改名迁移。

默认容器仅含Web与BM25诊断依赖，不包含CUDA模型和权重。界面/请求须显式选择 `bm25_diagnostic` 和 `utf8_diagnostic`；诊断用字节不是模型token。真实Qwen双源检索使用下文原生GPU环境，不会自动降级为诊断模式。

容器运行API/Worker使用非root用户。Nginx保留完整 `/api/v1` 前缀，支持SPA深层路由刷新、原文件和HTML/PDF下载；POST/模型任务不做上游自动重试。业务上传上限默认100MiB，Nginx总multipart限制110MiB以容纳边界和元数据。生成是持久任务，Nginx请求不等待长模型调用。

## 容器启动与停止

将 `deploy/compose.env.example` 复制到仓库外的私有配置文件，设置强数据库密码和可选生成模型档。不要把真实配置或 `docker compose config` 的展开结果提交GitHub。应用数据库采用分字段配置构造URL，密码中的特殊字符由Settings转义；显式非空 `APP_DATABASE_URL` 优先。

Windows PowerShell：

```powershell
.\scripts\start.ps1 -EnvFile C:\private\dalun\compose.env
# 受控服务器可加 -Server；保留当前镜像可加 -NoBuild。
.\scripts\stop.ps1 -EnvFile C:\private\dalun\compose.env
```

Linux：

```bash
bash scripts/start.sh /etc/dalun/compose.env server
bash scripts/stop.sh /etc/dalun/compose.env server
```

停止脚本只执行 `stop`，不会执行 `down -v`。更新前停止创建任务，等待或明确取消现有工作，先验证备份，再停止应用并升级；不要对运行中的文件/数据库做未协调复制。旧阶段2或原型卷应通过经验证的备份恢复到新部署，不能因项目名变化把空库当成已迁移数据。

## Windows/Linux原生CUDA

使用 `deploy/compose.native.yml` 启动仅绑定localhost的PostgreSQL基础设施；或配置已有PostgreSQL16。Web环境安装 `backend/requirements-web.txt`，模型环境沿用阶段2隔离CUDA解释器。编辑 `deploy/native.env.example` 的绝对数据目录、模型解释器及冻结tokenizer路径。

PowerShell先迁移，然后在两个终端分别运行API与Worker：

```powershell
.\scripts\native.ps1 -Role migrate -EnvFile C:\private\dalun\native.env -Python C:\venvs\dalun\Scripts\python.exe
.\scripts\native.ps1 -Role doctor -EnvFile C:\private\dalun\native.env -Python C:\venvs\dalun\Scripts\python.exe -RetrievalProfile qwen_cuda -VerifyCuda
.\scripts\native.ps1 -Role api -EnvFile C:\private\dalun\native.env -Python C:\venvs\dalun\Scripts\python.exe
# 第二个终端：
.\scripts\native.ps1 -Role worker -EnvFile C:\private\dalun\native.env -Python C:\venvs\dalun\Scripts\python.exe
```

Linux同一服务角色：

```bash
DALUN_PYTHON=/opt/dalun/web-venv/bin/python bash scripts/native.sh migrate /etc/dalun/native.env
DALUN_PYTHON=/opt/dalun/web-venv/bin/python bash scripts/native.sh doctor /etc/dalun/native.env --retrieval-profile qwen_cuda --verify-cuda
DALUN_PYTHON=/opt/dalun/web-venv/bin/python bash scripts/native.sh api /etc/dalun/native.env
# 第二个终端：
DALUN_PYTHON=/opt/dalun/web-venv/bin/python bash scripts/native.sh worker /etc/dalun/native.env
```

`deploy/systemd/` 提供原生Linux服务模板，部署者需调整 `/opt/dalun`、`/etc/dalun/native.env` 和 `/var/lib/dalun`，给予专用账号必要读写权限。模板不自动执行数据库迁移，不包含密码。已有模型缓存应可读，运行制品写入明确的数据目录。

## 就绪与预检

| 检查 | 含义 |
| --- | --- |
| `/health/live` | API进程存活 |
| `/health/ready` | 当前schema及可写存储就绪 |
| `/health/workers` | registry至少有一个未过期idle/busy Worker，不代表任务必定成功 |
| `python -m app.operations.health worker --max-age-seconds 60` | 容器/服务管理器检查Worker心跳，不输出主机名或数据路径 |
| `python -m app.operations.preflight --retrieval-profile bm25_diagnostic` | Web依赖、schema、存储与配置检查；不发送生成请求 |
| `python -m app.operations.preflight --http-base http://127.0.0.1:8080 --http-only` | 经同源入口检查live/ready/workers |

GPU检查必须显式 `--retrieval-profile qwen_cuda --verify-cuda`，实际启动指定解释器检查torch CUDA设备；未检查时标记 `cuda_verified=false`，不冒称GPU可用。生成档只记录配置状态与模型名，始终 `network_verified=false`，不把已填密钥当成远端连通。

## 冷备份、验证与恢复

`app.operations.cli` 是私有运维工具。先停止API/Worker写入，确认无queued/running任务，再明确传 `--quiesced`；该标记是操作人的停机声明，不是分布式锁。资产目录和数据库必须分别位于其正确位置，备份目标必须为全新目录，不能嵌在原assets内。备份原件与模型输出可能含私有资料，应保存在受控目录，不应作为公开交付清单提交。

SQLite诊断配置使用SQLite backup API和integrity_check。PostgreSQL使用与服务端匹配的PG16 `pg_dump/pg_restore`；密码通过PG环境变量传给工具，不放命令行或摘要。普通backend镜像不额外安装PG客户端，需在具备PG16客户端、Web依赖以及同一assets挂载的维护环境执行；原生部署可直接使用该环境。不要对活动Docker卷做文件级拷贝后宣称一致热备。

在backend目录、显式设置 `DALUN_ENV_FILE` 或数据库环境字段后：

```bash
python -m app.operations.cli backup /private/backups/backup-20261026 --quiesced
python -m app.operations.cli verify-backup /private/backups/backup-20261026
# 设置指向预先创建的新空PG库或新SQLite文件的显式目标配置，再恢复：
python -m app.operations.cli restore-backup /private/backups/backup-20261026 --assets-target /private/restored-assets-new --quiesced
```

恢复先校验manifest、相对路径、链接及所有文件SHA256；拒绝已有assets目标、已有SQLite文件、非空PG库。PG恢复单事务执行，不drop、clean或覆盖已有数据库。失败只可能留下本次新建目标，工具不自动递归删除现有路径；检查后用新的目标重试。恢复时使用与API/Worker一致的OS账号（容器UID10001），或只对本次全新恢复目录正确调整所有权后挂载；维护root生成的0755目录可能导致非root应用无法写入。恢复后先执行schema/文件、来源与可写就绪验证，再启动API与Worker。

## 交付清单

```bash
python -m app.operations.cli delivery-manifest /path/to/repo --output /private/delivery-manifest-new.json
```

默认清单仅收录源代码/明确配置白名单，排除.env与凭据、运行数据、模型、原件、node_modules、缓存、测试产物及历史临时副本。路径相对、记录字节数和SHA256，输出不能覆盖旧清单；显式 `--path` 仍执行敏感路径校验。常见密钥扫描不能识别任意自选口令，交付前仍应复核差异和配置。清单不包含环境秘密，也不能独自证明已完成真实API/部署验收。

## 专用部署fixture验收

`deploy/compose.acceptance.yml` 只用于全新隔离测试项目，明确使用synthetic模型名。正式compose没有fixture key/server或mock回退。专用provider仅在Worker容器loopback监听；API只冻结同名档，实际HTTP调用发生在Worker。

```bash
docker compose --env-file /private/ci.env -f compose.yml -f deploy/compose.acceptance.yml up -d --build
docker compose --env-file /private/ci.env -f compose.yml -f deploy/compose.acceptance.yml exec -d worker python -m app.operations.fixture_provider --acknowledge-synthetic-fixture
python scripts/smoke-deployment.py --state /private/acceptance-new.json --acknowledge-synthetic-fixture
docker compose --env-file /private/ci.env -f compose.yml -f deploy/compose.acceptance.yml restart backend worker frontend
python scripts/smoke-deployment.py --state /private/acceptance-new.json --verify-state --acknowledge-synthetic-fixture
```

烟测仅接受全新空公司库，真实经过Nginx上传合成PDF/TXT、独立Worker解析/索引/HTTP评价、引用原件核对和双格式导出；重启后只读验证来源、运行快照和报告哈希，零次额外模型请求。它验证工程流程，不验证真实模型质量或法规语义支持。本机缺少Docker，容器运行结果应由CI及部署环境记录；当前不宣称已完成外部服务器真实部署。

配置依据：[Docker启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)、[Compose include](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/)、[Nginx proxy_pass](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass)、[PostgreSQL16备份](https://www.postgresql.org/docs/16/backup-dump.html)。
