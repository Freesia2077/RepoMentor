# P0–P2 交付与验收

日期：2026-09-23。代码位于 `python/`；原 TS 版源码、启动脚本、配置与 SQLite 未被迁移程序改写。所有本地 Python 操作使用 `D:\anaconda\envs\repomentor`。

## 交付状态

| 阶段 | 已交付 | 验收边界 |
| --- | --- | --- |
| P0 | 环境前后审计、隔离后的依赖锁、SDK/CLI/上游版本、20 仓库清单、去重、60 条种子、旧 8 仓库 SHA 与 gold 审计 | 60 条种子已逐条源码核对，尚未人工签核；Torch/CUDA 仅元数据检查，没有宣称训练可用 |
| P1 | 快照、受控工具、Harness、预算、Ledger、阶段与取消、结构化事件、独立 SQLite、可安装包、TS/Python 对照 | Git blob 与 checkout 使用相同字节时对照；不假定跨平台换行字节数相同 |
| P2 | Claude SDK、CLI 三阶段报告、可信资产、MCP、权限审计、schema 校验、有限修复、分类错误、用量与轨迹 | 当前端点是 DeepSeek Anthropic-compatible；未部署 Qwen/MiniMind，未声称回答完全正确 |

原计划中“60 条人工核验”的人审部分保持未完成，不用模型核对代替人审签字。其余 P0 技术准备与 P1/P2 已提供可运行产物和真实记录。

## 验证证据

- 原 TS 历史测试：149 通过，1 个 live 测试跳过，0 失败；原始机器日志留在本地，本次没有重新执行 TS 测试。
- Python 首轮 39 项通过（含 TS/Python 对照）；发布复查后默认套件 **47 项通过、1 项历史 TS 对照未选中、0 失败**。公开记录见 `experiments/p1/release-review.json`，原始 pytest XML 留在本地。覆盖越界路径、Git symlink 对象、未提交改动隔离、UTF-8/字节预算、发现去重、阶段、取消、Ledger 补读、引用降级、SDK 模拟事件和错误、MCP handler 绕过 hook 后的 Harness 拒绝、schema、语料划分和 60 题证据哈希；新增导出无副作用、顺序复用 runtime 的用量隔离、事件续写、Python 测试文件与导入发现回归。
- 官方 SDK 的真实 MCP 调用：`developit--mitt-discovery.json`，3 次有效调查动作，结构化计划通过。
- 真实取消：`live-cancellation.json`，返回 cancelled，临时 cwd 已移除；取消前完成的 1 次合法工具动作仍被保留，不冒称回滚。
- 真实报告：mitt、nanoid 两例，以及旧回归集全部 8 例，都完成 Explorer → Mentor → Contributor。
- 离线复核：`experiments/p2/offline-verification.json`，重新验证保存结果的 schema、阶段和预算，没有新 API 调用。其 gold 路径召回是诊断指标，不是答案正确率。
- wheel 构建通过，包含受信任 Agent、Skill 与 `.claude-plugin/plugin.json`；运行不依赖源码仓库根目录加载 prompt。
- 隔离后的 `pip check` 通过；没有安装或加载 Torch、Qwen/MiniMind 权重。
- `run.ps1` → CLI → 本地 Git 快照 → stub 三阶段报告已实际运行成功；lint 与 format check 通过。交付文件检查没有发现当前模型 API key。

## 真实结果的解释

10 份报告的成功表示工作流完成、结果满足 schema、文件读取受约束。已保存 mitt 大小写表述矛盾和 nanoid Node/浏览器实现比较错误，详见 `sdk-migration.md`。没有做足够的人审或受控 TS/模型质量对照，因此不声称质量提升或回答准确率 100%。

所有报告都绑定固定 SHA；旧 gold 中不存在的文件已单独纠正并保留原始版本。原旧 benchmark 的“召回提升 10 个百分点”等门槛没有在本轮冒充已验证。

## 成本约束

用户在运行过程中要求减少 API 调用。检查时，已经启动的 8 仓库批量回归全部完成，因此直接复用其结果；之后只运行离线验收。实验脚本增加缓存复用，批量脚本默认不触发缺失记录的 API 调用。

用量与 SDK 成本字段保存在单次结果里。第三方兼容服务的真实收费应以供应商账单为准，不能按 SDK 的 Claude 计价推算为实际扣费。

## 运行入口

参见 `../README.md`。无需启动前后端；`doctor`、`snapshot`、`report`、`export` 已可使用。`index/chat/eval` 的通用实验入口以及 Qwen Runtime 在后续阶段实施，没有添加空壳命令。

## 下一阶段

按用户授权再进入 P3：部署本地 Qwen3.5-4B、测量显存与吞吐、实现 ChatCompletionsRuntime 和通用批量评测。已有仓库、contracts、读取边界和结果记录可直接复用；人审种子继续作为单独事项。
