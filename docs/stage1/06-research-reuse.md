# 小论文方法与代码复用映射

## 依据与定位

本次按用户指定，以本地小论文《年报与法规双源RAG_跨年与Agent实跑修订稿.docx》为第四章主要参考，位于研究工程的 results/paper/generalization_extension_20261023_01。

论文文件 SHA256：

`451aee611ed6c1091079f51d7a0f6afb1a1a536a6c4e76ca287f8798f588e01f`

研究工程本轮读取时 HEAD 为 `fba017153d0e2f8e955c29db503880b7c9125e93`。HEAD 仅记录仓库位置，不证明工作区没有未提交修改；实际复用时必须计算所选源码与依赖清单哈希。论文描述、源码存在和新系统集成成功是三种不同证据。

开题报告给出四维评价及Web系统方向；具体第四章流程遵循本轮指定小论文，不机械沿用旧仓库“先抽取年报事实、再只检索法规”的实现。

## 方法映射

| 小论文机制 | 已核对的研究代码（相对 xiaolun） | 新系统落点 | 迁移时验证 |
| --- | --- | --- | --- |
| 年报身份、文件哈希、物理页和页内区间 | src/data/document_corpora.py::load_annual_report_chunks、pdf_text_frames.py | 文档导入与解析适配 | 同一页文本与区间哈希一致，完整年报身份保留 |
| PDF/DOCX/TXT法规及结构/页面映射 | document_corpora.py::load_regulation_file_chunks、page_mapped_chunks.py、docx_text.py | 法规版本、文本帧、多段片段 | 不虚构页码，跨页片段逐段定位 |
| 双作用域 | document_corpora.py::DualCorpusRetriever.search | DualRetrievalService | 年报先按公司年度限定pool再排序；空pool不能全库回退 |
| BM25、稠密、融合与重排 | src/retrieval/bm25.py、dense.py、hybrid.py、reranker.py | RetrievalAdapter | 两路同片段身份；保存原始/融合/重排分数与模型revision |
| 模型身份与可校验索引制品 | src/retrieval/index_artifact.py、src/pipeline.py::load_dual_corpus_index | ArtifactIndexBackend | 源文件/清单/编码器身份校验，映射到数据库片段 |
| 完整片段预算、冻结上下文与来源记录 | src/generation/comparison.py::_pack_context、page_reference_protocol.py | ContextBuilder | 不随意截断引用片段；保存实际token数、上下文与来源哈希 |
| 来源角色和时间元数据限制 | joint_verification.py、src/retrieval/temporal.py | ClaimValidator、TemporalPolicy | 公司事实用年报、规范用法规、评价需两者；日期未知保留未知 |
| 工具补检索及预算 | supplementary_retrieval.py、scripts/run_generalization_extension_20261023.py、run_bounded_joint_rag.py | 可选Agent与调用账本 | 限轮数/调用数/上下文，保留失败和实际工具记录 |
| 真实调用与协议记录 | src/generation/llm_client.py、experiment_protocol.py | ModelGateway与t_model_call | 配置快照、请求/响应、timeout与attempt一致；不导出密钥 |
| 页级与片段级评价区别 | src/metrics/page_reference.py、span_retrieval.py、reviewed_generation.py | 实验结果展示与评分导入 | 指标名匹配标签粒度，人工/自动评分分别展示 |

## 初始参数和历史实验分开

研究工程 default.yaml 中的 Qwen3-Embedding-0.6B 与 Qwen3-Reranker-0.6B、CUDA、float16、batch_size=1 可作为本地模型档起点。配置默认生成 mock=true，且默认temperature等字段并不等于论文主试验实跑参数。不得直接把默认yaml复制后称为论文复现。

论文主试验描述的候选上限为合并10或双路各5，上下文上限为6000参考tokens。参考上限不等于实际token数，也不等于API费用。系统要保存参考分词器身份及服务端usage，不能把二者混用。

论文同时报告角色提示无稳定额外收益、Agent扩展不足以支持稳定增益，以及自动评分的天花板。因此默认固定双源、Agent可选，界面和论文系统描述不预设Agent更优。

## 适配和可移植性策略

1. 确定最小可复用模块及依赖闭包，记录来源HEAD、文件哈希和必要许可信息；不直接复制所有历史脚本、数据与输出目录。
2. 为 Chunk、SourceLocator、RetrievalHit、ContextSnapshot、GenerationResult 建立显式边界DTO。研究层不直接访问 Web ORM。
3. 增加单独 research_adapter 包。不得直接 import 通用的 src 包造成导入冲突；开发阶段可配置研究根目录，交付前将选定模块移入独立命名空间并固定依赖。
4. 研究层当前会自动读取其工程 .env；适配时改为显式传入配置，由系统进程环境优先注入，不把研究绝对路径或其密钥写入仓库。
5. 先在单年报/少量法规上验证解析与索引，再用冻结问题做原型/系统候选和上下文对照；不在首轮集成时重新跑全量历史生成。
6. 历史索引制品含原路径时建立相对路径映射。服务器不能依赖 Windows 用户目录；校验失败时不能跳过来源完整性检查。
7. 索引若只能恢复文档哈希而缺少原件，则保留历史只读回放能力；新正式评价要求来源可核查并显示缺口。
8. 复用现有真实语料不等于将原语料公开。公开仓库保存清单格式、导入脚本、哈希及必要的脱敏样例。

## 第四章建议组织

- 4.1 任务定义及双源证据角色。
- 4.2 文档身份、来源定位和知识组织。
- 4.3 双作用域检索与完整片段预算。
- 4.4 引用约束、论断与证据关联及不足处理。
- 4.5 可选工具Agent与运行记录。
- 4.6 实验设计、结果与适用边界。

第五章描述系统需求、架构、数据库、模块实现、界面及系统测试。第四章历史研究结论不自动成为新系统实测结果；第三章接入后单独说明指数与文本评价的关系。
