# 前端视觉与动效

本轮优先前端改版，保留现有公司、文档、评价、实验和报告业务。

## 设计方向

银白背景 #f5f5f7、白色表面 #ffffff、正文 #1d1d1f、辅助文字 #62636b、蓝色强调 #0066cc、展示区 #12151d。采用系统中文字体，桌面双栏导航与内容布局，移动端横向导航。首页双源纸张构图强调研究主题，数据仍来自真实接口，未知值保持缺失。

参考苹果发布会的排版层级和节奏：首屏标题与来源展示分层出现，路由短距离淡入淡出，点击流程标签切换说明，按钮按压反馈，弹窗沿用组件过渡。无滚动劫持、无循环装饰动效；系统prefers-reduced-motion和界面“减少动效”均可关闭非必要动效，用户偏好保存在浏览器。键盘可操作流程标签，提供跳至主要内容入口。

## 设计 skill

项目内安装 Anthropic frontend-design，目录 `.agents/skills/frontend-design`。上游：https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design ，Apache-2.0，保留原始SKILL.md与LICENSE.txt。选择理由是框架无关、强调内容与排版、可直接用于现有Vue工程；不宣称存在客观统一的“最佳”排名。

个人技能目录的安装连接未完成，未声称其已在个人Skills目录启用。项目内副本已用于本轮设计，并随项目版本管理。

## 验证

保持既有7条浏览器业务验收，新增桌面导航/动效偏好持久化、系统减少动效与375px布局验收。执行结果以对应PR的CI为准。模型路径沿用显式合成验收资料，不代表真实模型效果。

## 最终验收

代码已通过[PR #6](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/pull/6)合并main。验证源 `ad813aedd9262e08ff60d7c0645a5bf7206ca39d`，合并提交 `0801bfe62e363dcd06c0677361e72dcdaa404a14`，CI run 40437747791 全部成功：Linux507、Windows503（4项PG由Linux覆盖），Vue生产构建6.91秒，Chrome9条浏览器测试通过。容器部署、重启持久化和PG冷备恢复均通过。

已人工检查桌面与375px中文截图，未见主要内容裁切、横向页面溢出或中文缺字；窄屏宽表格通过表内横向滚动阅读。截图为合成验收数据。CI使用runner预装Chrome；中文截图使用既有PyMuPDF中的离线字体补齐runner的CJK显示，字体文件和临时CSS仅供验收，不进入正式构建源码。

[浏览器截图与报告制品](https://github.com/2391552910-coder/da-listed-company-self-discipline-evaluation/actions/runs/40437747791/artifacts/12704873315)。详细数据见[test-summary.json](test-summary.json)。
