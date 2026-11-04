# SWE-QA：20题检索与 Agent 复查

完成同题五组100份回答，以及排序修复后的同20题混合组复验，共120份回答、204次本地模型调用，无付费API。原回答和失败全部保留。

## 结果与成本

以下均为20题均值，正确性是五维量表中的一项，**不是准确率**。

| 阶段／方法 | 正确性 / 20 | 总分 / 100 | 模型调用 | 仓库操作 | 每题平均秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 首轮·闭卷 | 3.95 | 41.30 | 20 | 0 | 8.54 |
| 首轮·普通 BM25 | 11.30 | 65.95 | 20 | 0 | 12.25 |
| 首轮·代码增强 BM25 | 9.55 | 59.25 | 20 | 0 | 10.71 |
| 首轮·代码增强混合重排 | 9.05 | 58.35 | 20 | 0 | 11.42 |
| 首轮·Agentic RAG | 13.25 | 72.55 | 104 | 84 | 83.72 |
| 修复复验·混合重排 | 12.05 | 68.65 | 20 | 0 | 13.94 |

首轮普通BM25比闭卷高24.65分；代码增强反而使检索退化。修复后混合组提高10.30分，同题11高、7平、2低。排除生成前已发现重大参考错误的Pytest第23题，仅作敏感性分析，修复差值仍为+9.37；主表保留全部20题。Agent表格是修复前实现，没有补跑修复后Agent，不能与复验行冒称同版本比较。

这些是四仓库小样本的模型辅助评分，不证明全量基准或显著泛化提升。延迟包含本机模型换入及工具处理，使用已准备索引；首次下载、切块、向量构建未计入，不作为通用服务性能基准。统计原件见 [metrics.json](metrics.json)。

## 方法与范围

按 [SWE-QA 官方说明](https://github.com/peng-weihan/SWE-QA-Bench)读取指定 commit 的源码。复用已下载的 Git 快照，不执行目标仓库。固定数据 revision `f048f07da234b39e32937f115d027c5b71acd54e`，Flask、Requests、Pytest、Streamlink 各抽5题，种子20261103，排除既有题库及上轮90题；生成前冻结 `selection.json`、`source.json`、`plan.json`。

| 条件 | 实际取证与执行 |
| --- | --- |
| 闭卷 | 仓库名＋题目；无源码、历史或参考答案，允许已有知识 |
| 普通 BM25 | 共享语法切块、关键词排序和原文打包，生成一次 |
| 代码增强 BM25 | 增加现有符号、路径与一跳静态调用候选，生成一次 |
| 代码增强混合重排 | BM25＋Qwen3-Embedding-0.6B＋FAISS＋RRF＋Qwen3-Reranker-0.6B，再用相同代码增强器 |
| Agentic RAG | 与混合重排组逐题相同的初始源码，OpenAI Agents SDK Runner 按需补查；最多6次模型调用、6次仓库操作 |

全部使用现有 Qwen3.5-9B Q4_K_M、16K窗口、2048最终输出、temperature=0、seed=42。初始6个源码单元、8000字节，候选20个。Agent另有SDK提示、导航及多轮推理预算，不能将组间差值单独归因于工具。日常默认Agent仍是代码增强BM25/CPU，本表Agent为匹配混合检索的对照。

参考官方五维量表，正确性、完整性、相关性、清晰度、推理各1–20分，总分5–100。当前Codex逐题辅助评审，非盲评、非人工批准，也不是论文的GPT-5 judge；详见 `scoring-protocol.json`。SWE-QA没有gold源码片段，不报告伪造的Recall@K。题目来自公共发布池，不声称未进入模型预训练或代表完整720题。

## 实现复查

- “固定RAG”只是检索一次后生成一次的历史名称。普通BM25、代码增强及混合检索均可走这条路径；Agent由SDK决定后续取证。
- 沿用 `retrieval_factory → chunking/retrieval → neural_retrieval/hybrid_retrieval → code_retrieval → RagEngine/SDK`，评测没有另一套切块、向量化或Agent循环。普通BM25无需向量模型。
- 源码按函数、类、文档标题切分；超长单元继续按行约束。768是UTF-8字节上界，不是模型token数；Embedding与完整生成输入各自再核对真实token预算。
- 源码块及BM25索引保存在JSON；1024维、L2归一化的向量保存在NumPy缓存，FAISS `IndexFlatIP`执行余弦检索；RRF后可重排。SQLite只存运行与请求记录，没有独立向量数据库服务。
- 四库共22,116块。逐库核对FAISS Top-20与暴力内积一致，全部向量单位范数通过；见 `preflight.json`。两项遗漏分别为Pytest大文档与Streamlink不可读文档，已列明。
- 评测入口原先将同一个检索器复制给各条件，可能使向量/混合标签实际运行BM25；现复用原 `policy_for` 正确选择条件，并去掉重复准备。旧90题本来就只选BM25，此接线错误不解释旧差值，但漏掉其他组是评测范围错误。
- 实际旧90份闭卷输入均为两条消息，源码上下文为空；本轮20份准备输入再次核对，参考答案始终只用于评审。闭卷猜测与RAG有据回答的规则不同，不能解释为纯上下文消融。

## 失败驱动的修复

通用英文词 `runner/test/stream/plugin` 被误作精确符号，相关夹具获得高优先级，挤掉BM25/向量已经召回的实现。修复限定在原符号索引与候选排序：长叙述需要代码标记，短导航查询保留；弱匹配不另占证据通道。全部20题的混合组复验在 `repair-design.json` 预先声明，保持模型、提示、切块及原检索候选不变，复用合法缓存。已曝光题的修复复验属于开发结果。

离线核对20题的底层候选完全一致，闭卷和普通BM25的40份消息逐字不变，增强后17题的源码上下文改变，见 [retrieval-change-audit.json](retrieval-change-audit.json)。Flask第9题恢复CLI依赖链，49→93；Pytest第48题恢复真实比较方法，59→95。Streamlink第23题取到工厂后由31→67，仍未完整解释设计动机。

恢复证据并不保证作答成功：Streamlink第33题完整的 `test_dash_stream_url:120–134`、去重注释和JSON断言已实际发送，模型仍拒答，31→30；Requests第24题措辞引入错误，94→90。其余仍有匿名问题定位失败、类型/分支推导错误。Agent出现10次已读范围重读和2次重复动作，分别保留工具错误，未写新循环或提示补丁。详见 [failure-analysis.json](failure-analysis.json)。

参考答案问题见 [reference-audit.json](reference-audit.json)：1项重大前提错误和2项局部问题；原参考与全部题目不改写。100份首轮及20份复验均通过JSON/来源/请求核验，但不代表120份答案正确，原件含不完整正文和无据断言。

## 复用与检查

本轮只修改既有 `benchmark.py`、`code_navigation.py`、`code_retrieval.py` 和现有测试，没有新增运行模块或实验脚本，运行模块仍57个。复查明细见 [implementation-review.json](implementation-review.json)。完整离线检查为372 passed、1 deselected，Ruff通过；见 [validation.json](validation.json)。

输入、配置、参考答案及输出分别存于两组 `dataset.json`、`results.jsonl`；源码身份变化前后各自完成离线核验，收据在各组 `verification.json`。主评分见 [rubric-scores.jsonl](rubric-scores.jsonl)，复验见 [repair-rubric-scores.jsonl](repair-rubric-scores.jsonl)。首轮和复验方法源码分别封存在 [method-source.zip](method-source.zip) 与 [repair-source.zip](repair-source.zip)，私有配置、缓存和权重不入包。

原90题及其三题诊断保持封存：[旧报告](../public-sweqa-2026-11-03/README.md)。后续优先处理已检索证据的利用与未知问题定位；这批已曝光评测题不作为训练数据，不继续增加模块或扩大实验条件。
