# 实验原件索引

当前结论以[SWE-QA 50题五组评测](../experiments/sweqa-50-2026-11-03/README.md)为主，评分与划分规则见[数据协议](data-protocol.md)。本页只保留复核和面试需要的入口，不维护逐轮调参日志。

## 公开评测

| 目的 | 原件 |
| --- | --- |
| 当前结果与成本 | [总报告](../experiments/sweqa-50-2026-11-03/README.md)、[分批/逐题指标](../experiments/sweqa-50-2026-11-03/metrics.json) |
| 250份答案与评分 | [答案/轨迹](../experiments/sweqa-50-2026-11-03/combined/results.jsonl)、[五维评分](../experiments/sweqa-50-2026-11-03/combined/rubric-scores.jsonl) |
| 数据与固定源码 | [选题与SHA](../experiments/sweqa-50-2026-11-03/selection.json)、[题目与参考](../experiments/sweqa-50-2026-11-03/source.json) |
| 可信度与失败 | [失败分析](../experiments/sweqa-50-2026-11-03/failure-analysis.json)、[真实请求来源核验](../experiments/sweqa-50-2026-11-03/wire-source-audit.json)、[工程检查](../experiments/sweqa-50-2026-11-03/validation.json) |
| 旧20题复用来源 | [复用核验](../experiments/sweqa-50-2026-11-03/reuse20-verification.json)；保持原评分，不挑最好答案 |

旧20题已用于开发，新增30题生成前冻结；公开均分不等于准确率。代码增强未获得总体正收益，Agent相对混合也有逐题退步及额外成本，均在原报告保留。

## 面试案例与业务切片

| 案例 | 原件及解释 |
| --- | --- |
| Flask-042与Pytest-017 | [失败分析](../experiments/sweqa-50-2026-11-03/failure-analysis.json)：前者说明初始证据边界，后者说明已有原文仍误读；不是都由工具补查改善 |
| Requests JSON/files | [两臂输出](../experiments/business-steps-submission/results.jsonl)、[来源复核](../experiments/business-steps-submission/summary.json)：实际补回覆盖赋值；工具组5次模型/3次仓库操作，对照1次/0次，未隔离新增信息和额外推理 |
| TinyDB无参删除测试 | [修复前](../experiments/new-question-q4-confirmation/summary.json)、[补查后](../experiments/literal-test-recall/summary.json)：同一道已知失败完整通过，不能当成新题泛化 |
| 旧8题业务开发 | [冻结控制](../experiments/business-steps-control/summary.json)、[SDK结果](../experiments/business-steps-submission/summary.json)：当前事实/严格完整5/8、必要决定6/8，仍2份严重错误，未过当时门槛；不与公开50题混分母 |
| 两个模型代理交叉试用 | [冻结任务](../experiments/business-developer-trial/dataset.json)、[答案与来源](../experiments/business-developer-trial/results.jsonl)、[配对汇总](../experiments/business-developer-trial/summary.json)：额外查读28→19但总源码更多；真人参与0，不证明人类提效 |

讲述和离线命令集中在[面试稿](interview-playbook.md)，原理集中在[技术问答](rag-interview-assessment.md)，简历只使用[标准版](resume-project.md)。

## 历史证据与方法

历史批次继续保留原始JSON/JSONL、评审包、失败、来源和哈希清单。旧模型、旧预算与开发诊断不能累加成当前准确率；未受清单约束且仅重复原件的Markdown展示已清理。

原P6.3的[comparison.json](../experiments/p63/runs/confirmation-v1/comparison.json)和案例JSON保留；其展示副本可由已有`experiment_review.py`及`p63_runner.py`重新导出。旧文件中的参数只描述当时条件。

[研究汇总](../experiments/improvement-study/report/report.md)及[方法源码包](../experiments/improvement-study/method-source.zip)是历史归档。[冻结协议](improvement-study-protocol.md)本身参与报告输入哈希，保持原文，仅供核查旧批次，不是当前待执行计划。

```powershell
.\run.ps1 scripts/verify_archives.py
.\run.ps1 -m repomentor.cli questions verify
.\run.ps1 -m repomentor.cli inspect-run experiments/sweqa-50-2026-11-03/combined/results.jsonl --case-id sweqa-pytest-017--bm25_code
```
