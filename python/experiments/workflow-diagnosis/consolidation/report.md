# 保存结果的离线诊断

来源批次：rag-consolidation；18 份回答；新增模型请求 0。

复用原模型非盲评审，不重新打分。信号用于定位可观测损失，不能自动认定因果。

| 分组 | 回答 | 核心可用 | 完整解决 | 原请求 |
| --- | ---: | ---: | ---: | ---: |
| answerable/agentic | 8 | 4 | 0 | 17 |
| answerable/hybrid_rerank | 8 | 4 | 1 | 8 |
| unanswerable/agentic | 1 | 0 | 0 | 2 |
| unanswerable/hybrid_rerank | 1 | 0 | 0 | 1 |

以下展示前 24 份有诊断信号的记录，全部记录见 cases.json；不是精选成功率。

| case_id | 来源 | 信号 |
| --- | --- | --- |
| q-35441446c0f3e154--hybrid_rerank | developit/mitt | reference_complete_incomplete_answer |
| q-f33bdd28fc012276--hybrid_rerank | tkem/cachetools | reference_complete_incomplete_answer |
| q-4cfe308994156f5d--hybrid_rerank | chalk/chalk | reference_complete_fact_error, citation_support_issue |
| q-f33bdd28fc012276--agentic | tkem/cachetools | reference_complete_incomplete_answer |
| q-4c35fdb83cf995e0--agentic | psf/requests | reference_gap_at_input, agent_gap_without_new_source, workflow_failure |
| q-f94258ac48ef550c--agentic | tj/commander.js | reference_complete_incomplete_answer |
| q-1803ac7237f279cd--hybrid_rerank | theskumar/python-dotenv | abstention_flag_mismatch |
| q-bc525cfef5654a48--hybrid_rerank | msiemens/tinydb | reference_complete_incomplete_answer |
| q-b73aeb622c828d8e--hybrid_rerank | ai/nanoid | reference_complete_fact_error, citation_support_issue |
| q-4c35fdb83cf995e0--hybrid_rerank | psf/requests | reference_gap_at_input, citation_support_issue |
| q-1803ac7237f279cd--agentic | theskumar/python-dotenv | abstention_flag_mismatch |
| q-71b59398a21ab6c3--hybrid_rerank | tkem/cachetools | reference_gap_at_input, citation_support_issue |
| q-bc525cfef5654a48--agentic | msiemens/tinydb | reference_complete_incomplete_answer |
| q-71b59398a21ab6c3--agentic | tkem/cachetools | reference_gap_at_input, agent_gap_without_new_source, citation_support_issue |
| q-35441446c0f3e154--agentic | developit/mitt | reference_complete_incomplete_answer |
| q-b73aeb622c828d8e--agentic | ai/nanoid | reference_complete_fact_error, reference_complete_incomplete_answer, citation_support_issue |
| q-4cfe308994156f5d--agentic | chalk/chalk | reference_complete_fact_error, citation_support_issue |

Signals locate observable losses, not proven causes. Reference coverage is a surrogate; candidate lists may omit other channels. Research/finalization losses require source-linked review. Original nonblind reviews are reused, not independent human review; batches must not be pooled.

原批次及其源码评审保持封存；本报告仅验证输入字节和评审绑定，不代替源码语义复核。
