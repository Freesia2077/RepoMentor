# 非后训练阶段：离线派生报告

状态：**complete**。本报告生成不调用模型；所汇总回答记录实际新增 78 次模型请求。真人参与者 0。

语义判断只来自已绑定原始结果的源码评审；格式成功、引用行号合法和参考覆盖不替代答案正确性。两个批次、首次与重复分别展示。

## improvement-finalization

complete；输出 12/12，已评审 12，封存 True。

Six selected known questions; one recorded finalizer call per arm, only response schema differs. Not new generalization, not complete agent episodes, not human review. Source/notes/prompt/seed/token limits fixed; no retry. All failures retained and excluded from training.

| 分组/条件 | 事实 | 核心 | 完整解决 | 完整解决 Wilson 95% | 完整性 adequate / Wilson95% | 新请求 | 已观测输入/输出 token | 统一整段耗时中位数秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_answerable/finalizer_candidate | 2/5 | 2/5 | 1/5 | 4%–62% | 4/5 / 38%–96% | 5 | 16975/2406 | 17.9 |
| first_answerable/finalizer_control | 3/5 | 3/5 | 0/5 | 0%–43% | 1/5 / 4%–62% | 5 | 16975/810 | 7.6 |
| first_private/finalizer_candidate | 0/1 | 0/1 | 0/1 | 0%–79% | 1/1 / 21%–100% | 1 | 4679/609 | 27.3 |
| first_private/finalizer_control | 1/1 | 0/1 | 0/1 | 0%–79% | 1/1 / 21%–100% | 1 | 4679/93 | 4.1 |

区间仅是对当前小样本计数的二项假设描述；题目由实现者选择，不能当作总体准确率区间。pending 分组不把缺评审算成错误或成功。

| 分组 | 对照→候选 | 事实增/失 | 核心增/失 | 完整增/失/双过/双未过/待评 | 不一致对中候选胜率精确95% |
| --- | --- | --- | --- | --- | --- |
| first_answerable | finalizer_control→finalizer_candidate | 0/1 | 0/1 | 1/0/0/4/0 | 2%–100% |
| first_private | finalizer_control→finalizer_candidate | 0/1 | 0/0 | 0/0/0/1/0 | — |

| 分组/条件 | 初始→最终参考非空行全部覆盖 | 工具调用/有效新增/已读重复 | 新增非空行/最终保留 | 新取证 episode 中完整解决 | 无新取证 episode 中完整解决 | cache hit |
| --- | --- | --- | --- | --- | --- | --- |
| first_answerable/finalizer_candidate | 5→5 | 0/0/0 | 0/0 | 0/0 | 1/5 | 0 |
| first_answerable/finalizer_control | 5→5 | 0/0/0 | 0/0 | 0/0 | 0/5 | 0 |
| first_private/finalizer_candidate | 0→0 | 0/0/0 | 0/0 | 0/0 | 0/1 | 0 |
| first_private/finalizer_control | 0→0 | 0/0/0 | 0/0 | 0/0 | 0/1 | 0 |

原始失败：无已保存失败。


| 私有信息 case | 评审事实/拒答 | insufficient_evidence |
| --- | --- | --- |
| q-1803ac7237f279cd--finalizer_candidate | incorrect/appropriate | False |
| q-1803ac7237f279cd--finalizer_control | correct/appropriate | False |
## improvement-confirmation

complete；输出 36/36，已评审 36，封存 True。

Ten author-selected questions in two newly added public Python repositories; eight answerable and two private-state questions. Same BM25 code-aware initial sources. Tools on/off use the same stage policy and 3-decision limit; actual paths may end early. Two preselected questions have separate-cache repeats at identical seed, reported separately. No final-test opening, no training, no human participants. Candidate schema rejected by the prespecified development gate, so the existing production schema is retained.

| 分组/条件 | 事实 | 核心 | 完整解决 | 完整解决 Wilson 95% | 完整性 adequate / Wilson95% | 新请求 | 已观测输入/输出 token | 统一整段耗时中位数秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| first_answerable/agentic | 3/8 | 2/8 | 0/8 | 0%–32% | 3/8 / 14%–69% | 21 | 88393/5045 | 25.6 |
| first_answerable/agentic_no_tools | 4/8 | 2/8 | 0/8 | 0%–32% | 1/8 / 2%–47% | 16 | 54125/7600 | 38.6 |
| first_answerable/bm25_code | 3/8 | 1/8 | 0/8 | 0%–32% | 1/8 / 2%–47% | 8 | 15711/1550 | 9.6 |
| first_private/agentic | 1/2 | 1/2 | 1/2 | 9%–91% | 1/2 / 9%–91% | 4 | 11488/868 | 20.2 |
| first_private/agentic_no_tools | 2/2 | 2/2 | 2/2 | 34%–100% | 2/2 / 34%–100% | 4 | 9415/605 | 14.4 |
| first_private/bm25_code | 1/2 | 1/2 | 1/2 | 9%–91% | 1/2 / 9%–91% | 2 | 3039/415 | 9.1 |
| repeat/agentic | 0/2 | 0/2 | 0/2 | 0%–66% | 0/2 / 0%–66% | 5 | 18285/2087 | 43.4 |
| repeat/agentic_no_tools | 1/2 | 1/2 | 0/2 | 0%–66% | 0/2 / 0%–66% | 4 | 13506/2499 | 49.2 |
| repeat/bm25_code | 2/2 | 1/2 | 0/2 | 0%–66% | 0/2 / 0%–66% | 2 | 4056/283 | 7.0 |

区间仅是对当前小样本计数的二项假设描述；题目由实现者选择，不能当作总体准确率区间。pending 分组不把缺评审算成错误或成功。

| 分组 | 对照→候选 | 事实增/失 | 核心增/失 | 完整增/失/双过/双未过/待评 | 不一致对中候选胜率精确95% |
| --- | --- | --- | --- | --- | --- |
| first_answerable | bm25_code→agentic_no_tools | 3/2 | 2/1 | 0/0/0/8/0 | — |
| first_answerable | bm25_code→agentic | 2/2 | 2/1 | 0/0/0/8/0 | — |
| first_answerable | agentic_no_tools→agentic | 1/2 | 1/1 | 0/0/0/8/0 | — |
| first_private | bm25_code→agentic_no_tools | 1/0 | 1/0 | 1/0/1/0/0 | 2%–100% |
| first_private | bm25_code→agentic | 0/0 | 0/0 | 0/0/1/1/0 | — |
| first_private | agentic_no_tools→agentic | 0/1 | 0/1 | 0/1/1/0/0 | 0%–98% |
| repeat | bm25_code→agentic_no_tools | 0/1 | 1/1 | 0/0/0/2/0 | — |
| repeat | bm25_code→agentic | 0/2 | 0/1 | 0/0/0/2/0 | — |
| repeat | agentic_no_tools→agentic | 0/1 | 0/1 | 0/0/0/2/0 | — |

| 预选重复题/条件 | 首次完整→重复完整 | 四项质量分档相同 | 答案 JSON 相同 |
| --- | --- | --- | --- |
| improvement-confirm-blinker-02/agentic | False→False | True | True |
| improvement-confirm-blinker-02/agentic_no_tools | False→False | True | True |
| improvement-confirm-blinker-02/bm25_code | False→False | True | True |
| improvement-confirm-tenacity-01/agentic | False→False | True | True |
| improvement-confirm-tenacity-01/agentic_no_tools | False→False | True | True |
| improvement-confirm-tenacity-01/bm25_code | False→False | True | True |

| 分组/条件 | 初始→最终参考非空行全部覆盖 | 工具调用/有效新增/已读重复 | 新增非空行/最终保留 | 新取证 episode 中完整解决 | 无新取证 episode 中完整解决 | cache hit |
| --- | --- | --- | --- | --- | --- | --- |
| first_answerable/agentic | 0→0 | 10/7/1 | 274/274 | 0/5 | 0/3 | 0 |
| first_answerable/agentic_no_tools | 0→0 | 0/0/0 | 0/0 | 0/0 | 0/8 | 0 |
| first_answerable/bm25_code | 0→0 | 0/0/0 | 0/0 | 0/0 | 0/8 | 0 |
| first_private/agentic | 0→0 | 0/0/0 | 0/0 | 0/0 | 1/2 | 0 |
| first_private/agentic_no_tools | 0→0 | 0/0/0 | 0/0 | 0/0 | 2/2 | 0 |
| first_private/bm25_code | 0→0 | 0/0/0 | 0/0 | 0/0 | 1/2 | 0 |
| repeat/agentic | 0→0 | 2/2/0 | 16/16 | 0/1 | 0/1 | 0 |
| repeat/agentic_no_tools | 0→0 | 0/0/0 | 0/0 | 0/0 | 0/2 | 0 |
| repeat/bm25_code | 0→0 | 0/0/0 | 0/0 | 0/0 | 0/2 | 0 |

原始失败：

- `improvement-confirm-tenacity-04--agentic_no_tools`：failed；error=invalid_citations；finish=None；stop=invalid_citations；answer_present=False。原始 completion 保留在 cases.json。

| 私有信息 case | 评审事实/拒答 | insufficient_evidence |
| --- | --- | --- |
| improvement-confirm-blinker-05--agentic | unverifiable/appropriate | True |
| improvement-confirm-blinker-05--bm25_code | unverifiable/appropriate | True |
| improvement-confirm-blinker-05--agentic_no_tools | correct/appropriate | True |
| improvement-confirm-tenacity-05--agentic_no_tools | correct/appropriate | True |
| improvement-confirm-tenacity-05--agentic | correct/appropriate | True |
| improvement-confirm-tenacity-05--bm25_code | correct/appropriate | True |

P2 门槛结论：Reject candidate and retain the existing production schema for new confirmation.

The private-information protection case acquired new incorrect library claims; both flags remain false.

## 口径与限制

- evaluation_elapsed_seconds 使用各 case 相同外围计时；保存的 generator 耗时另列。未知 token/耗时保持缺失计数，缓存记录不伪装成新请求成本。模型与量化、上下文、seed、决策/工具上限、设备声明保留在 summary.json。
- 工具有效新增要求返回此前未见的非空原文行；工具名称、命中元数据或合法行号不算新增。还报告新增行在最终输入的保留数。该指标不保证语义相关、被正确使用或带来因果收益。
- 参考完整度只是预设行段覆盖代理；有效替代来源可能不在参考中。空 final_context 保持为空，不能用曾读内容补齐本次可见证据。
- 工具开/关匹配阶段策略和上限，实际路径可能提前结束；固定单次 RAG 与多次推理 SDK 的差异不能全部归因于工具。相同 seed 的两题重复只显示运行稳定性，不扩大独立样本数。
- 模型辅助评审与子 Agent 复核不改称人审；没有真人节时、满意度或 ROI 测量。两个新公开仓库仍可能出现在预训练数据中，原最终测试保持封存。
- 全部数据不进入 SFT/DPO/GRPO，未开展后训练。P2 候选不通过保护门槛时保留原默认，不根据新确认结果再追调。

硬件与冷/热声明：

```json
{
  "cpu": "AMD Ryzen 7 7840H w/Radeon 780M Graphics",
  "gpu": "NVIDIA GeForce RTX 4060 Laptop GPU",
  "vram_mib": 8188,
  "generator_gpu_offload": "Observed 29/33 layers (28 repeating layers plus output); context slot 16384. These are this-run observations, not guaranteed settings for other hosts.",
  "driver_version": "581.94",
  "generator": "Project-managed portable Ollama 0.34.3, loopback 127.0.0.1:11435, Qwen3.5-9B Q4_K_M, 16K context, one concurrent request",
  "development_warmup": "No extra warmup generation. First development request includes cold model load; subsequent requests may reuse loaded weights.",
  "confirmation_warmup": "Same service continued after 12 development calls; no explicit reload or warmup generation. Per-request raw times retained; keep_alive and OS state are not controlled.",
  "index_cache": "Preparation builds CPU indexes before generation. Run constructs an index object for each new snapshot/policy using the existing disk cache, then reuses objects within the run. Preparation cost is not included in per-case time.",
  "answer_cache": "Independent per-case cache directories, including repeat cases. No result cache hits intended; recorded cache_hit checked separately.",
  "timing": "evaluation_elapsed_seconds uses monotonic time around model/index acquisition and the entire answer call for every condition. It excludes offline preparation, prior cases, agent review and human reading. Existing elapsed_seconds fields have different scopes and are not used for end-to-end comparison.",
  "other_gpu_work": "This task started no other GPU workload; operating-system/display/background utilization was not isolated.",
  "observed_after_development": {
    "used_vram_mib": 7119,
    "gpu_utilization_percent": 14
  },
  "human_participants": 0
}
```
