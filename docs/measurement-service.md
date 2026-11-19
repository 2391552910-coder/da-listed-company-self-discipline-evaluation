# 可选主体自律指数测度服务

默认使用 `compose.yml` 启动主系统。资料管理、检索、四维问答、实验和报告导出按原有流程运行。左侧「主体自律指数」页面会显示测度服务状态，主系统的就绪检查仅检查自己的数据库和文件存储。

## 接入测度

将正式发布的 `indicators.json` 与 `weights.json` 放入主机目录，并让容器用户可读。设置 `DALUN_MEASUREMENT_DATA_DIR` 指向该目录，推荐使用仓库外的绝对路径。可通过原环境文件配置该变量。

```bash
docker compose --env-file deploy/local.env -f compose.yml -f compose.measurement.yml up -d --build
```

Compose 将数据目录只读挂载到独立服务。测度进程只在内部网络提供 HTTP 接口，主 API 提供浏览器访问的同源入口。使用其他 Python 基础镜像仓库时，可设置 `DALUN_MEASUREMENT_BASE_IMAGE`。

独立进程也可在本地 Web 环境用以下命令运行。先设置 `MEASUREMENT_DATA_DIR` 指向正式资料，再在主 API 的环境中设置 `MEASUREMENT_SERVICE_URL=http://127.0.0.1:8100`。

```bash
cd backend
python -m uvicorn app.measurement.api:app --host 127.0.0.1 --port 8100
```

## 正式输入约定

| 文件 | 必填字段 |
| --- | --- |
| `indicators.json` | `version` 为数据发布版本。`rows` 为公司年度数组，每条含 `stock_code`、`report_year`、`indicators`。股票代码是六位字符串，指标为 `I01` 至 `I12` 的正式标准化数值，范围为 0–100。 |
| `weights.json` | `version` 为权重发布版本，`dataset_version` 对应数据版本。`weights` 含 `I01` 至 `I12` 的最终权重，权重非负且合计为 1，每个维度至少有一个正权重。 |

`D1` 对应 `I01`–`I03`，`D2` 对应 `I04`–`I06`，`D3` 对应 `I07`–`I09`，`D4` 对应 `I10`–`I12`。总体指数为标准化值按正式权重的加权平均。维度分数为维度内加权平均，其权重按维度内合计重新归一。结果带数据与权重版本和文件 SHA256。权重训练、指标标准化和正式版本审批属于资料发布流程。

缺文件时返回 `inputs_missing`，资料格式或版本不一致时返回 `inputs_invalid`，数据中无该公司年度时返回 `not_found`。默认未启用为 `disabled`，已启用但连接失败为 `unavailable`。查询结果缺失时 `result` 为 `null`。

## 主系统接口

- `GET /api/v1/measurement/status` 查看测度开关及输入状态。
- `GET /api/v1/measurement/results/000001?report_year=2024` 查询对应公司年度。

问答的冻结 HTML/PDF 与检索实验报告来自各自运行快照，主体自律指数由测度页面单独展示。
