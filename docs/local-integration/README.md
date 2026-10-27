# 本机完整后端联调（进行中）

2026-10-27。本轮目标是本机PostgreSQL、API、独立Worker与同源前端联通，再验证文档、检索、评价和报告。

已核实：本机Docker29.8.0可用，独立docker-compose5.5.1可用，docker compose插件不可用；工具进程PATHEXT仅.CPL，PowerShell调用原生EXE失败。启动兼容层优先解析.exe，支持插件/独立命令回退，并只在原生调用期间补齐进程PATHEXT后恢复。支持-NoBuild及-Wait健康等待；stop保留数据库与文件卷。

已有PLM容器占用5432及其他服务8000，本轮使用独立项目dalunlocal20261027、独立命名卷，前端预定127.0.0.1:5173。未修改或停用PLM容器。

DockerHub拉取遭遇认证网络EOF，已由GitHub构建应用离线镜像并下载到本机；镜像不含私有配置。打包来源为本轮开始时main，镜像制品run40446098574，artifact12709000099（保留7天）。本机缓存PostgreSQL16.15-alpine可用，compose的DALUN_POSTGRES_IMAGE可显式选择它，默认仍为16-bookworm。

私有环境配置位于仓库外，显式读取本机xiaolun配置，未将密钥、真实资料或私有配置提交GitHub。

**本机执行环境在镜像下载完成后断开。镜像SHA校验、docker load、完整容器启动、API/Worker就绪、业务端到端与真实生成均尚未完成，不能将镜像构建或CI通过当成本机联调成功。** 恢复连接后需先读取本机现有文件并同步本PR修复，校验离线包SHA256，导入镜像，显式选择缓存PG镜像，再执行start.ps1 -EnvFile PRIVATE -NoBuild -Wait。

## 已验证的代码交付

[PR #8](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/pull/8)已合并main，源提交 `33a1e42d81ad3061cae47eec1d30c359cef73929`，合并提交 `d674e89168861c2f4aae5a5320e12682abd61dfc`。[CI 40446910590](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40446910590)全部成功：Windows原生EXE桩启动专项、Linux507、Windows503（4项PG由Linux覆盖）、Chrome11条流程、容器与PG恢复通过。它们是代码/CI验证，不能代替本机服务运行验收。

恢复连接后的执行顺序：

1. 从main同步本PR的compose.yml及scripts下PowerShell修复，保留仓库外runtime.env原有数据库口令。
2. 对下载的dalun-application-images.tar.gz与SHA256SUMS做本机哈希核对，通过后docker load。
3. 本机若继续使用已缓存postgres:16-alpine，在私有env显式设置DALUN_POSTGRES_IMAGE=postgres:16-alpine，避免拉取被阻断的bookworm镜像。
4. 使用start.ps1 -EnvFile PRIVATE_ENV -NoBuild -Wait启动隔离项目；不要接入或停用现有PLM数据库。
5. 验证/health/ready、/health/workers及同源/api/v1/dashboard，退出前端图表演示模式。
6. 验证公司/年报/法规导入、Worker解析、显式检索档、评价、引用原文与HTML/PDF导出、重启持久化。真实模型配置已在私有文件中，但连通性与真实生成尚未验证；不得用合成响应冒称真实模型成功。

详见[test-summary.json](test-summary.json)。
