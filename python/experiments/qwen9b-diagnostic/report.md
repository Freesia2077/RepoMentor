# Qwen3.5-9B：本机部署与开发集诊断

2026-10-19。**RTX 4060 Laptop 8GB / 约 16GB 内存已运行 Qwen3.5-9B Q4_K_M，保留 16K 上下文。** 9B 是独立候选配置，4B 默认配置、权重和历史结果保留。局部质量改善不足以宣布 RAG 达标，下一步应处理续读和最终输出的信息损失。

## 部署与实际资源

- 使用原有便携 Ollama 0.34.3、Conda 环境与 OpenAI Agents SDK；没有更换推理框架或自研 Agent 循环。
- 官方来源 `qwen3.5:9b-q4_K_M`；独立配置为 `config/model-settings.qwen35-9b-16k.json`。权重、别名、参数及分词器身份见 [deployment.json](deployment.json) 与 [download.json](download.json)。
- 模型包约 6.65 GB。源码读取测试使用 3 次本地请求、1 次成功 `read_code`；实际驻留窗口为 16,384。日志显示 **30/33 层在 GPU**，CUDA 权重约 4,398 MiB、主机权重缓冲约 959 MiB，属于 CPU/GPU 混合运行。
- [smoke.json](smoke.json) 的全设备采样峰值 **7,531 MiB**，含桌面和其他进程；模型在 `/api/ps` 中报告的显存分配约 5.57 GB。采样可能遗漏短暂峰值，这不是全部任务的最大显存保证。
- 该测试明确要求读取指定文件行段，用于验证加载、工具链及配置窗口；不作为自主检索能力或满 16K 长文质量测试。
- 官方 9B 分词器的 [LFS 指针](https://huggingface.co/Qwen/Qwen3.5-9B/raw/main/tokenizer.json) 与原缓存 SHA256 一致，核验后复用，没有盲用 4B 分词器。完整权重校验及网络中断恢复见 [download-recovery.json](download-recovery.json)。临时下载辅助程序已清理。

## 诊断条件

6 道已有开发题，覆盖入口对比、大小写测试、浅拷贝、生产数据缺失、代理参数和蓝图注册。它们是选定的诊断题，**不是新确认集、陌生仓库测试或无偏准确率估计**；原确认集没有用于本轮调参。

| 条件 | 说明 |
| --- | --- |
| 普通检索 `sdk` | 两模型共享相同的 CPU / 语法块 BM25 初始结果，之后可通过 SDK 工具补查 |
| 人工源码起点 `oracle` | 人工核对的关键原文替换初始片段；仍使用同一 SDK、工具和预算，可以继续补查 |
| 共同设置 | 16K、研究上限 1024、最终上限 1536、temperature=0、top_p=1、seed=42、thinking 关闭 |
| 共同额度 | 每题最多 4 次模型请求、4 次工具动作、240 秒；源码总量 18,000 字节、单次工具观察 6,000 字节 |

题目、源码起点、评审要点与实现身份在生成前冻结：[protocol.json](protocol.json)、[tasks.json](tasks.json)、[评审要点](evaluation-only/answer-checklist.json)。参考答案文字没有输入模型。人工源码起点是信息诊断，不是完美上限；它也允许工具补查。不同模型的训练、量化细节与底层模板不被严格隔离，因此这是部署组合对照，不是纯参数量消融。

## 结果

所有实际输出均由 Codex 阅读源码并非盲评审；不是人工签核或官方榜单。正确性、引用支持与完整性分别判断，严格解决要求它们同时达标且无错误拒答。接受语义等价解释与有效替代引用，不要求机械引用全部参考区间；边界判断理由保存在逐题评审中。

| 条件（每组 6 题） | 流程完成 | 结论正确 | 严格解决 | 请求总数 | 使用工具的题数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4B 普通检索 | 6 | 4 | **2** | 16 | 2 |
| 9B 普通检索 | 6 | 4 | **3** | 19 | 4 |
| 4B 人工源码起点 | 5 | 4 | **3** | 12 | 0 |
| 9B 人工源码起点 | 6 | 6 | **4** | 15 | 2 |

9B 人工源码条件的“结论正确 6/6”包含只回答“不一样”的不完整答案，**不能写成 100% 任务准确率**。普通检索条件中，9B 修正了入口结果混淆，也出现了新的错误符号解释；总体只有一题严格解决增益，样本太小，不能声称稳定显著提升。

普通检索条件平均请求数约为 4B **2.67**、9B **3.17**；已记录的 SDK 阶段平均耗时约为 **15.4 / 24.0 秒**。这些时间不含初始索引/检索，并混有模型加载、提示缓存及不同工具动作，不能当作纯解码速度或通用性能基准。详见 [summary.json](summary.json)、[judgments.json](judgments.json) 与 [review-decisions.json](review-decisions.json)。

## 关键归因

1. **更强模型有局部理解收益。** `history-dev-03` 普通检索中，4B 把两入口说反，9B 正确回答 non-secure 返回空串、Node 抛 RangeError。两者都进行了补查。
2. **最终阶段会损失已经获得的解释。** 9B 的 `history-dev-04` 研究阶段正确解释 `FOO/foo` 等测试断言，最终正文却只有“不转小写”；测试原文仍完整进入最终输入。4B 的人工源码入口对比在研究末尾已自行纠正，最终答案又将结果翻转。当前“整理”阶段只接收原文与问题，**不接收研究草稿**，实际上再次独立作答；这不只是截断或摘要长度问题。
3. **读到同一文件不等于读到关键声明。** 9B 的 Flask 补查实际只覆盖到第 149 行等片段，没有拿到第 172 行的真实标志，最终误将 `first_registration` 当成 `_got_registered_once`。4B 在普通检索下也未定位到声明，但选择拒答。
4. **不能只增大外层预算。** 单次 `read_code` 还有最多 100 行、原文最多 3,200 字节的内部限制。将观察上限从 6,000 增至 12,000 字节，仍未使原动作返回第 172 行，见 [离线预算复演](read-budget-diagnostic.json)。保持原工具限额，按 `next_line=150` 追加续读可取到真实标志，见 [continuation-check.json](continuation-check.json)。这是零生成的工具反事实，不是模型自主选对动作或答案改善的证明；它也不证明能在相同模型请求数内完成。
5. **9B 仍会产生逻辑扩张。** Requests 回答把“空字典”直接等同于“命中 no_proxy 列表”，遗漏其他绕过原因或空代理配置，不能因为主要结论有出处就判整段完全正确。

因此，优先工作是让 SDK 流程正确处理未完成的读取范围，并保留已核验结论、减少最终独立重答造成的信息损失。当前证据不支持继续单纯扩大总窗口，也不支持直接把剩余问题交给 MiniMind 训练或推断 27B 必能解决。

## 成本、保护与回放

本轮 **65 次本地模型请求**：加载/读取检查 3、4B 配对 28、9B 配对 34。共享总账 **447/600**，本批上限 72；付费与 DeepSeek 均为 **0**。没有重复生成失败题，4B 的 `invalid_citations` 失败保留。

24 条输出全部完成逐项评审，全部 `training_eligible=false`。65 次请求的实际用量均已知，最大实际输入与输出合计 **7,033 token**，加预留后均在 16K 内，thinking 均关闭：[usage-audit.json](usage-audit.json)。因此，本轮没有在满 16K 输入上验证语义质量。

[审计](audit.json)确认模型起点一致、预算及来源通过，原 344 份历史文件、590 份 P6.3 文件、117 份收尾文件保持不变。[严格回放](network-free-replay.json)已实际恢复 24 条原尝试，整个进程拒绝 HTTP 发送：生成 0、元数据请求 0、HTTP 尝试 0。

相关离线回归 **30 passed**，新运行器与统一入口静态检查通过。没有改动 TS 应用。RAG v1 原质量验收仍未通过，本轮开发诊断不覆盖或替代旧结论。

## 使用与复现

从 `python/` 执行，9B 已下载时不用再次准备：

```powershell
.\scripts\ollama_local.ps1 start
.\run.ps1 -m repomentor.cli ask --snapshot-id 9999db891acf16c1a1fd96b5 --settings config/model-settings.qwen35-9b-16k.json --question "get_jobs() 不传 tag 返回内部列表本身吗？"
```

以上使用本机已有 Schedule 快照并会生成新答案；普通 CLI 的默认决策额度与本次诊断不同，不是本批次的重跑命令。查看已保存输出不需要启动模型服务：

```powershell
.\run.ps1 -m repomentor.cli inspect-run experiments/qwen9b-diagnostic/cases/9b--history-dev-05--sdk.json --output artifacts/qwen9b-example.md
.\run.ps1 scripts/p63.py model-probe audit
.\run.ps1 scripts/p63.py model-probe replay
```

`run` 自动复用保存记录，包括失败；`replay` 禁止 HTTP。重现生成需要匹配的模型、快照、实现与私有缓存；改动实现后使用新身份，不覆盖本轮结果。
