# 本机后端联调记录（2026-10-27）

## 范围和结果

本记录来自 Windows 主机 DESKTOP-M1C1SSQ 的实际执行，不是 CI 结果。独立 Compose 项目 `dalunlocal20261027` 运行在 [http://127.0.0.1:5173](http://127.0.0.1:5173)。PostgreSQL 只在 Compose 网络中使用，未发布 5432；没有停止或修改现有 PLM 容器和 8000 服务。`repo` 是保留的旧快照，实际执行代码为独立 Git 检出；原私有 `runtime.env` 和密码保留，追加 `DALUN_POSTGRES_IMAGE=postgres:16-alpine`，未使用 `compose.local.yml`。Docker Server 29.8.0、独立 Compose 5.5.1、PostgreSQL 16.15。

镜像归档 SHA-256 `8ac2fe1a80f93f575ccbc1ed693d72fcc6534b5db561a4fcc5c31bda92744e77` 与清单一致，已导入。应用镜像来自 [构建 40446098574](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40446098574)，源码 `35c36c2d24ea5085f66b4be504b47692a004a685`；该提交与交接 main `3c27548727c9bca3179d19eeb1e5f16cb31fac35` 的 backend/frontend 无差异。本次仅基于该前端镜像重新打包 nginx.conf 修复，未联网重建应用。

迁移成功，API、Worker、PostgreSQL、前端均健康；同源 `/health/ready`、`/health/workers` 和 `/api/v1/dashboard` 返回 200。真实浏览器显示本机数据库的公司、文档、索引和评价记录。创建了标明合成的公司、2024 年测试 PDF 年报和 TXT 法规；Worker 实际解析并各生成一个分块，构建 `bm25_diagnostic` 双源索引、检索到两个来源，原文字符偏移和 PDF 物理页码可回查，上下文任务成功。模拟模型的一题评价成功、两条证据声明，HTML（83809 字节）和 PDF（1724723 字节）成功导出。测试数据和模拟生成不构成真实论文实验或真实公司评价。单次业务执行通过 API 完成；浏览器验证了真实数据展示，未逐页通过界面重复全部操作。

真实模型分别进行两次单题、每次最多一次调用：第一次 `MODEL_TRANSPORT_FAILED`，第二次 `MODEL_HTTP_ERROR`；额外一个最小请求返回 HTTP 403。容器 DNS、TCP、TLS 连通，具体拒绝原因尚未确定；消耗情况以服务商账单为准。当前配置已恢复真实模型，模拟服务已随 Worker 重建移除。真实模型回答、真实资料验收仍未完成。BM25 是诊断检索，未验证 Qwen/CUDA 或向量模型。第三章 AHP-Transformer 评分尚未接入，不生成虚构分数。

停止并重新启动后，公司、文档、索引、上下文及评价记录保留；模拟报告也可重新列出。发现 Nginx 原先缓存 backend 地址，容器重建变更地址时导致 502。本次使用 Docker DNS 动态解析；本机占用旧地址后重建 backend，地址发生变化，前端不重启即恢复 `/api/v1/dashboard`。已增加 CI 回归场景；`nginx -t` 通过。

## 启停

在本次独立检出目录执行，先把 Docker Desktop 的 `resources\\bin` 加入**当前 PowerShell 进程**的 PATH，再使用仓库脚本（不要打印私有 env 内容）：

```powershell
$env:PATH = 'C:\Users\mech-mind\AppData\Local\Programs\DockerDesktop\resources\bin;' + $env:PATH
$privateEnv = 'C:\Users\mech-mind\Documents\ChatGPT\New project\dalun\local-integration-20261027\runtime.env'
.\scripts\start.ps1 -EnvFile $privateEnv -NoBuild -Wait
.\scripts\stop.ps1 -EnvFile $privateEnv
```

停止保留卷。离线前端镜像已叠加本次代理配置；如果重新导入旧归档会覆盖标签，需重新构建包含本次 nginx.conf 的前端镜像。私有目录及测试产物不应提交。

## CI 与本机的区别

此前 [CI 40446910590](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40446910590) 的 Linux 507、Windows 503 通过（4 跳过）、浏览器 11、部署及备份恢复是历史 CI 结果，并非本轮本机结果。此次修复的新 CI 状态以本次 PR 为准。结构化结果见 [test-summary.json](test-summary.json)。
