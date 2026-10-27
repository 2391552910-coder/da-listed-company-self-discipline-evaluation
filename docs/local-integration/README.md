# 本机完整后端联调（进行中）

2026-10-27。本轮目标是本机PostgreSQL、API、独立Worker与同源前端联通，再验证文档、检索、评价和报告。

已核实：本机Docker29.8.0可用，独立docker-compose5.5.1可用，docker compose插件不可用；工具进程PATHEXT仅.CPL，PowerShell调用原生EXE失败。启动兼容层优先解析.exe，支持插件/独立命令回退，并只在原生调用期间补齐进程PATHEXT后恢复。支持-NoBuild及-Wait健康等待；stop保留数据库与文件卷。

已有PLM容器占用5432及其他服务8000，本轮使用独立项目dalunlocal20261027、独立命名卷，前端预定127.0.0.1:5173。未修改或停用PLM容器。

DockerHub拉取遭遇认证网络EOF，已由GitHub构建应用离线镜像并下载到本机；镜像不含私有配置。打包来源为本轮开始时main，镜像制品run40446098574，artifact12709000099（保留7天）。本机缓存PostgreSQL16.15-alpine可用，compose的DALUN_POSTGRES_IMAGE可显式选择它，默认仍为16-bookworm。

私有环境配置位于仓库外，显式读取本机xiaolun配置，未将密钥、真实资料或私有配置提交GitHub。

**本机执行环境在镜像下载完成后断开。镜像SHA校验、docker load、完整容器启动、API/Worker就绪、业务端到端与真实生成均尚未完成，不能将镜像构建或CI通过当成本机联调成功。** 恢复连接后需先读取本机现有文件并同步本PR修复，校验离线包SHA256，导入镜像，显式选择缓存PG镜像，再执行start.ps1 -EnvFile PRIVATE -NoBuild -Wait。
