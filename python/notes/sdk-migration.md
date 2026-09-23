# SDK 迁移与面试笔记

## P0：如何建立可以解释的基线

固定 TS 基准 `d1a4e7dfc6e8896a51c807be3c3b754297af1302`、Python 解释器、SDK、捆绑 CLI、上游模型及 GitHub commit。模型权重没有下载，后续不得仅凭模型标签代表不可变版本。

依赖安装发现 Python 用户级 site-packages 可能混入 Conda。解决方式是所有入口使用指定解释器和 `-s`，按隔离后的实际分发包生成锁文件，并通过 `pip check`。没有修改 Conda base 或另建 `.venv`。

一次可复现实验至少保存：固定问题/仓库、源 SHA、运行时及模型配置、输入上下文、实际观察、结束原因、预算、用量、耗时和输出。复现依赖服务的后端版本，冻结配置不意味着能保证远端模型永远逐字输出相同内容。

## P1：为什么 Harness 独立于模型和 UI

模型负责选择动作与解释，Harness 负责让动作合法且可观测。直接调用 SDK MCP handler 也必须经过阶段、白名单、去重和预算检查；SDK hook 不是唯一权限边界。

文件从 Git blob 读取，先校验 manifest、文件类型、大小，再校验内容 hash；symlink 和 submodule 不跟随。2 批、18 个文件、88,000 字节的旧报告全局上限保留。阶段分配分别为 10/8 个文件和 48k/40k 字节，Skill 可进一步收紧文件数量。发现工具有独立扫描上限。

Ledger 区分 available / partial / missing，保留尝试记录；重复发现被拒绝，计划可复用已经完成的发现；完整内容可复用，部分读取允许下一阶段补充。取消不会提交半批读取状态。引用检验只校验所见路径，不冒充事实蕴含验证。

TS/Python 对照使用同一字节内容的 fixture，比较规范化路径、选择、拒绝类别、预算和 Ledger 状态。Windows 的 CRLF checkout 与 Git 中 LF blob 是不同字节内容，不能拿它们的预算数字直接比较。Python 新增严格 commit、并发 fetch 私有 ref 与普通文本 2MB 安全扫描上限，这些差异有意保留。

## P2：官方 Python SDK 适配检查表

| 项目 | 处理与证据 |
| --- | --- |
| 版本 | `claude-agent-sdk==0.2.158`，捆绑 CLI `2.1.280`；不假定等同于旧 TS SDK 的行为 |
| 生命周期 | `ClaudeSDKClient` async context 管理；一次结构化会话使用 SDK 自己的循环 |
| 原生工具 | `tools=[]`，并禁用文件、shell、网络、Agent/Task 等工具；权限模式 dontAsk |
| MCP | 只注册当前 Skill 的 3 个发现工具；返回路径/计数，不返回原始源码；实际执行归 Harness |
| 配置 | 临时 cwd、独立临时 CLAUDE_CONFIG_DIR、空 setting_sources、strict_mcp_config；不加载目标仓库配置 |
| Prompts / Skills | 从受信任应用资产复制，保存哈希并随 wheel 打包；不从目标仓库读取 agent 指令 |
| Hooks | PreToolUse 审计允许工具，StructuredOutput 仅允许主会话提交；其他调用拒绝 |
| 结构化输出 | Pydantic schema 转 Draft-07 definitions；SDK 输出后再做 Pydantic 校验 |
| 修复 | 仅一次格式修复，新调用禁用发现工具并使用固定观察；没有静默 provider/runtime 降级 |
| 错误 | 取消、超时、认证、限流、服务不可用、SDK transport、输出能力缺失、schema 错误分别记录 |
| 真实兼容性 | 实际用户端点为 DeepSeek 的 Anthropic-compatible 服务；真实 MCP、三阶段报告与取消均已执行 |
| 用量 | 保留 SDK usage/model_usage/turns/cost；未知或兼容服务估算不能冒充供应商账单 |
| 可携带性 | wheel 含 prompts、skill.json、SKILL.md 与隐藏 plugin manifest；不依赖仓库根路径找到资产 |

SDK 会提示 `allowed_tools` 的自动许可使重叠的 can_use_tool 回调不触发，这是已知语义：逐次审计使用 PreToolUse，真正边界在 Harness；不能在面试中说“回调会拦截每次工具调用”。

## 真实报告的已知语义错误

结构通过不代表回答正确。首次真实运行中已根据固定源码识别：

- mitt 的 Mentor 输出同一段里把事件名区分大小写与“不敏感”混写，虽然实现确实不做归一化。
- nanoid 的 Mentor 声称 Node/浏览器实现“逐行对应，只有随机源不同”，但该 SHA 的 Node 实现有字符串池快速路径；还把 177 行的 Node 实现概括成“数十行”。

原始结果保留，不为了验收抹去错误。这些案例会成为后续 RAG/回答核验的开发样本，不回流最终封存测试。P2 验收的是接入、结构和约束；没有宣称质量超过 TS 版或已解决幻觉。
