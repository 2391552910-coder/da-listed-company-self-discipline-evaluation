# 阶段5：工作台与冻结报告

日期：2026-10-26（北京时间）。前端、导出及验收脚本已实现；最终CI与浏览器结果见测试摘要。真实模型实证边界见[阶段4](../stage4/README.md)。

## 页面与流程

公司登记 → 文档导入/解析 → 索引构建 → 新建单题或四维评价 → 持久任务/取消/重试 → 回答与论断 → 引用原文/原件 → HTML/PDF导出。刷新后从服务器读取运行，不依赖浏览器内存保存任务。

- `/evaluations/new` 显式选择公司、年度、索引、模板、模型及参考/调用/用量/超时预算。
- `/tasks` 展示实际进度、错误与运行入口，失败重试新建运行；部分完成保留原结果。
- `/evaluations/:id` 展示四维覆盖与缺证、每题状态、模型支持判断、机械核验及人工尚未复核，点击引用读取真实文本帧和原文件位置。
- `/reports-export` 仅对指定完成/部分完成且有正文的运行导出，保留导出历史，HTTP错误不另存成PDF。
- 公司、年度、总览使用真实接口计数；无第三章结果时显示未接入，不显示演示指数、固定等级或随机图表。
- 评价正文、模型输出和来源均按文本渲染，不使用v-html插入不可信内容。

## 报告契约

| 接口 | 行为 |
| --- | --- |
| POST /api/v1/evaluations/{id}/exports {format:html或pdf} | 201，CPU渲染并持久化，支持幂等键 |
| GET /api/v1/evaluations/{id}/exports | 指定运行的分页导出历史 |
| GET /api/v1/exports/{id} | 安全元数据、大小、文件/快照SHA256 |
| GET /api/v1/exports/{id}/file | 原冻结制品；HTML同源预览，PDF下载 |

导出为同步CPU渲染，区别于阶段1草案中的异步导出任务；不会触发检索或外部模型。原评价快照、渲染器版本、字体身份与制品哈希保存，下载只读已冻结文件，不再依赖原件/索引/模型可用。正文、失败维度、资料限制、逐题claims、来源位置、运行配置及调用用量完整保留；null和0分开，长文不截断。

HTML转义动态文本并提供CSP，JSON快照附录无脚本执行。原文件链接仅同源预览时可访问；离线HTML通过版本/物理页/字符区间核对，不把原始年报嵌入报告。

PDF使用已在Web依赖中的PyMuPDF，内嵌CJK字体，A4自动分页。检测缺字时明确报错，不静默省略。嵌入完整JSON快照作为附件，可逐字恢复。默认字体为PyMuPDF自带Droid Sans Fallback Regular，不依赖Windows字体目录。

## 验证

- 报告20项测试通过：中文字体实际嵌入、中文抽取、500段长文及结尾完整、JSON附件与冻结bytes一致、HTML注入转义、状态/null区分、幂等/冲突、文件篡改拒绝、历史下载不变。
- 真实PyMuPDF1.28.2生成6页fixture报告，Poppler逐页渲染，核查首页/正文中页/证据页/末页无裁剪、重叠或中文缺字。明确为合成验收数据，不是模型真实评价。
- 浏览器验收采用真实FastAPI、Worker、SQLite、文档解析、BM25、loopback HTTP Provider与报告渲染；来源和模型响应明确合成fixture，不作为研究质量证据。
- 本地npm registry连接被环境拒绝，浏览器与构建交GitHub CI执行，结果在测试摘要中如实记录。

CI浏览器命令（frontend目录）：

```bash
npm install
npm run build
npm install --no-save @playwright/test@1.56.1
npx playwright install --with-deps chromium
npx playwright test
```

Playwright自动启动隔离验收后端与Vite，检查新建单题、实际异步完成、刷新回溯、来源原文/原件与HTML/PDF下载。后端验收脚本仅监听127.0.0.1，不连接外部模型。报告、截图与失败trace由CI保存，真实模型和正式服务器验收不混入该结果。
