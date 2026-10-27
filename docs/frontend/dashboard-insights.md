# 研究数据看板

增加任务状态环图、四维来源覆盖条形图、双源资料构成。读取现有/dashboard接口，不增加模型调用。

范围：年报按公司与年度，法规属于共享库。任务含解析/索引/上下文/评价，失败或中止为failed/cancelled/budget_exhausted，不是质量评分。

覆盖比例分母为该维度已发布成功回答数，分子为包含所选来源片段的回答数。来源可切换双源、年报、法规。分母为零、接口失败或字段未知显示—，不伪装0分；重复与重试按运行计次，窗口截断和无法校验单独提示。来源入选不证明语义支持或法律适用。

前端仅在没有真实数据时提供“预览图表样式”；?charts=demo也可显式打开。演示数据有持续醒目标记，只存在图表组件内，不进入后端、报告、真实统计或质量比较。真实统计成功返回时自动退出演示。

CI保留原9条浏览器流程，新增真实接口计数/来源切换、服务不可用/显式演示/375px不横向溢出两条测试。生产构建包额外归档，用于无本机npm依赖时预览真实前端。

## 本轮验收

[PR #7](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/pull/7)已合并main。验证源 `44f330584f8dc0bee3ce29df2ed0e89f2b72e33a`；[CI 40443808735](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40443808735)的三项job全部成功：Linux507、Windows503（4项PG由Linux覆盖）、Chrome11条流程，Vue生产构建通过。正式容器和PG恢复通过。已查看中文桌面和375px截图，未见主要图表裁切或横向页面溢出。

本机预览 http://127.0.0.1:5173/?charts=demo 已更新；它是本机静态前端，尚未连接本项目后端，演示标记仅作用于新图表，其他业务统计继续显示未知。正式接口统计返回时自动使用真实数据。

详细摘要见[insights/test-summary.json](insights/test-summary.json)。[图表截图与浏览器报告](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40443808735/artifacts/12707850762)。
