# 保存结果的离线诊断

来源批次：rag-system；252 份回答；新增模型请求 0。

复用原模型非盲评审，不重新打分。信号用于定位可观测损失，不能自动认定因果。

| 分组 | 回答 | 核心可用 | 完整解决 | 原请求 |
| --- | ---: | ---: | ---: | ---: |
| answerable/agentic | 42 | 38 | 28 | 97 |
| answerable/bm25 | 42 | 25 | 17 | 42 |
| answerable/bm25_code | 42 | 31 | 24 | 42 |
| answerable/hybrid_rerank | 42 | 34 | 28 | 42 |
| answerable/none | 42 | 0 | 0 | 42 |
| answerable/oracle | 12 | 9 | 8 | 12 |
| unanswerable/agentic | 6 | 4 | 4 | 12 |
| unanswerable/bm25 | 6 | 4 | 4 | 6 |
| unanswerable/bm25_code | 6 | 4 | 4 | 6 |
| unanswerable/hybrid_rerank | 6 | 4 | 4 | 6 |
| unanswerable/none | 6 | 5 | 5 | 6 |

以下展示前 24 份有诊断信号的记录，全部记录见 cases.json；不是精选成功率。

| case_id | 来源 | 信号 |
| --- | --- | --- |
| q-e8efe7278b8bb71c--agentic | tkem/cachetools | workflow_failure |
| q-f9892f3a2a15836e--bm25_code | theskumar/python-dotenv | workflow_failure |
| q-7c6e7d6ff198d4f8--oracle | ai/nanoid | workflow_failure |
| q-e65fd233cb070da4--bm25_code | debug-js/debug | citation_support_issue, abstention_flag_mismatch |
| q-f94258ac48ef550c--none | tj/commander.js | evidence_control |
| q-262c1cf5abc5b82e--bm25 | msiemens/tinydb | reference_gap_at_input, reference_lost_in_packing, workflow_failure |
| q-342b7c23442b0be4--bm25_code | msiemens/tinydb | reference_gap_at_input |
| q-9651d7a4b096b6d7--none | pallets/flask | evidence_control |
| q-eae1c28b15286a51--bm25 | tj/commander.js | abstention_flag_mismatch |
| q-e65fd233cb070da4--agentic | debug-js/debug | abstention_flag_mismatch |
| q-ae6f6ee7cb6da33c--bm25 | msiemens/tinydb | citation_support_issue, abstention_flag_mismatch |
| q-f33bdd28fc012276--hybrid_rerank | tkem/cachetools | reference_complete_incomplete_answer |
| q-6f667ba286bf327e--bm25 | chalk/chalk | abstention_flag_mismatch |
| q-c4e449cd956c6738--none | dbader/schedule | evidence_control |
| q-e51b8504be45afa0--bm25 | debug-js/debug | reference_complete_fact_error, reference_complete_incomplete_answer, citation_support_issue |
| q-5e05fd09931239ef--none | tj/commander.js | evidence_control |
| q-dce4d164575a7561--agentic | theskumar/python-dotenv | citation_support_issue |
| q-e65fd233cb070da4--bm25 | debug-js/debug | citation_support_issue, abstention_flag_mismatch |
| q-f33bdd28fc012276--bm25 | tkem/cachetools | reference_complete_incomplete_answer, citation_support_issue |
| q-342b7c23442b0be4--none | msiemens/tinydb | evidence_control |
| q-f94258ac48ef550c--agentic | tj/commander.js | reference_gap_at_input, agent_gap_without_new_source |
| q-f33bdd28fc012276--agentic | tkem/cachetools | reference_complete_incomplete_answer |
| q-c697f19f4ade9013--none | tj/commander.js | evidence_control |
| q-7e67a58f5f3ce89d--bm25_code | more-itertools/more-itertools | reference_complete_incomplete_answer |

Signals locate observable losses, not proven causes. Reference coverage is a surrogate; candidate lists may omit other channels. Research/finalization losses require source-linked review. Original nonblind reviews are reused, not independent human review; batches must not be pooled.

原批次及其源码评审保持封存；本报告仅验证输入字节和评审绑定，不代替源码语义复核。
