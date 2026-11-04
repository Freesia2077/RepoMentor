# SWE-QA 公开子集评测

原固定 RAG 在本轮未证明整体增益；失败分析定位到符号排序问题，并用一个原地修复完成三题诊断。三题不并入主成绩。

## 主结果

| 条件 | 题数 | 总分 /100 | 正确性 /20 | 完整性 /20 | 标记证据不足 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 闭卷，允许使用已有知识 | 90 | 49.19 | 7.17 | 5.67 | 41/90 |
| 固定 RAG，原批次 | 90 | 46.98 | 5.76 | 4.62 | 59/90 |

同题平均差值 −2.21 分，按仓库聚类重采样 10,000 次的 95% 区间为 [−7.24, 2.90]；评分提高/持平/下降为 37/3/50 题。区间未涵盖评审模型自身的不确定性。证据不足是模型字段，部分回答仍包含推测，不能直接等同拒答率。

按 [SWE-QA 官方说明](https://github.com/peng-weihan/SWE-QA-Bench) 下载 15 个仓库的指定 commit，只读 Git 对象，不执行目标项目。GitHub 与 Hugging Face 的逐仓库问答内容一致。发布池为 720 题，每库在生成前固定随机选 6 题，排除本地已用题；汇总 split 不重复计数。版本、SHA、抽样规则见 [admission.json](admission.json) 和 [repositories.json](repositories.json)。这是 90 题公开子集，不是全量或完全未曝光测试。

两组均为原有 Qwen3.5-9B Q4_K_M、16K 总窗口、2048 输出、温度 0、seed 42，每题一次生成。RAG 使用现有语法 BM25 与代码增强，6 个源码单元、最多 8000 UTF-8 字节。没有评估 SDK Agent 或神经检索的总体效果。

评分参考[官方五维量表](https://github.com/peng-weihan/SWE-QA-Bench/blob/c13deac7a0d99b0ca2e593e004c4739475785b08/Benchmark%20construction/score/llm-as-a-judge.py)，每项 1–20，由 Codex 当前会话逐份做非盲模型辅助评审。不是人工批准、正确率或论文 GPT-5 分数；具体判分及结果/参考答案 hash 见 [rubric-scores.jsonl](rubric-scores.jsonl)，统计见 [metrics.json](metrics.json)。

## 失败与单一修复

- 精确符号被普通词抢占候选：`DataclassWithOneItem` 实际存在，候选池也有对应文件，但实现文件中的 `line/file/module` 等命中先占满六个符号槽位。修复仅调整现有 `SymbolIndex.lookup` 的命名标识符优先级和排序，见 [identifier-ranking.patch](identifier-ranking.patch)。
- 部分材料未进入索引或上下文：现有每库 2000 文件 /20MB 索引限制影响 Django、Pylint 等仓库；长类只交付局部窗口。容量遗漏见 [indexing-audit.json](indexing-audit.json)，不能将这些失败全归因于生成模型。
- 已有材料也会读错：开发预检中 Requests 的 `no_proxy` 把 None 与空值混淆；主批次 `pytest-003` 的正文截句但 JSON 仍成功。记录见 [failure-analysis.json](failure-analysis.json)。

在原批次封存后，仅复测三个已曝光失败；模型、问题和预算相同：

| 诊断题 | 修复前 | 修复后 |
| --- | ---: | ---: |
| pytest：定位测试数据类 | 36 | 89 |
| Django：保存后的权限与过滤条件 | 36 | 92 |
| scikit-learn：预测值绘图接口复用 | 36 | 76 |

三题均恢复目标源码并产生实质回答，最后一题仍缺完整委派链解释。这是选择性开发诊断，不能写成新版在 90 题上的总体提升。原批次分数和失败保留。

## 数据与核验

参考答案中有两处可确证的 Python 语义错误：`isinstance` 允许子类，`list.remove` 使用相等比较。源码及独立语言检查见 [reference-audit.json](reference-audit.json)。原参考未改，主统计保留全部 90 题；排除两题后的差值为 −2.44 分，方向一致。没有全量 gold chunk 标注，不报告检索 Recall@K。

共 187 次本地生成：预检 4、主评测 180、诊断 3；无付费模型 API。369 项离线测试通过、1 项按配置排除，Ruff 通过。原批次 180 条及诊断 3 条请求/来源已离线核验。主批次代码封存在 [method-source.zip](method-source.zip)，当前代码包含随后排序修复；当前 `benchmark verify` 对应诊断批次，原批次按封存代码与归档 hash 复核。

```powershell
.\run.ps1 scripts/verify_archives.py
.\run.ps1 -m repomentor.cli benchmark verify --output experiments/public-sweqa-2026-11-03/diagnostic
```
