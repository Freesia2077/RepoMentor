# Python 结构与 TS 版本的对应关系

Python 是独立的 CLI / 可调用库，当前保留仓库学习报告的三个阶段。它复用 TS 版业务契约与约束，使用官方 Python Claude Agent SDK；没有通过子进程调用 TS 服务器。TS 基准为 `d1a4e7dfc6e8896a51c807be3c3b754297af1302`。

## 调用路径

```text
cli.py / Notebook / 实验脚本
  ├─ repository.py → 固定 SHA、manifest、Git blob、仓库概况
  ├─ report.py → Explorer → Mentor → Contributor
  │    ├─ skills.py + assets/ → 可信提示词与工具策略
  │    ├─ runtimes/claude.py → SDK 自带的工具循环与结构化输出
  │    └─ harness.py → 阶段、权限、发现、读取、预算、引用、取消
  │         └─ tools.py → 符号搜索、静态依赖、相关测试
  └─ storage.py → 独立 SQLite、JSONL 事件、result.json
```

`contracts.py` 是跨入口使用的类型边界。`StubRuntime` 支持零模型调用的集成验证。SDK 接入只负责模型调用和工具协议，实际工具权限、读取预算与状态变更仍由 Harness 控制。

## 模块对应

| TS 基准模块 | Python 模块 | 保留与变化 |
| --- | --- | --- |
| `src/types/index.ts`、`src/lib/schema.ts` | `contracts.py` | 报告 camelCase 字段与三阶段语义保留；Pydantic 替代 Zod，运行记录另有版本字段 |
| `src/lib/repo.ts`、`repository-profile.ts`、`evidence-content.ts` | `repository.py`、`harness.py` | checkout 改为固定 SHA 的 Git 对象；不读未提交文件；概况是精简实现 |
| `src/lib/repository-tools.ts` | `tools.py` | 三个发现工具返回路径/计数；Python 导入支持相对路径和常见 src 布局 |
| `src/services/repository-harness.ts` | `harness.py` | 阶段、动作去重、Ledger、分批读取、引用约束；全局 2 批 / 18 文件 / 88,000 字节 |
| `src/services/harness-skills.ts`、根目录 `agents/` / `skills/` | `skills.py`、包内 `assets/` | 初次迁移复制可信资产并记录哈希；运行只用包内资产 |
| `src/services/claude-agent-runtime.ts` | `runtimes/claude.py` | 官方 Python SDK、隔离 cwd、受控 MCP、权限 hook、结构化输出和有限修复 |
| `src/services/pipeline.ts` | `report.py` | 三阶段报告、完整小仓库读取复用、取消和错误结果；无 HTTP 对象 |
| `src/services/model-settings.ts` | `config.py`、`paths.py` | Python 专属 JSON / `REPOMENTOR_PY_*` 与数据目录；不读取 TS 配置或共享模型变量 |
| `src/services/orchestrator.ts`、`src/db/`、路由与 SSE | `cli.py`、`storage.py` | 仅迁移独立运行记录；没有迁移 Web 任务队列、SSE、交互问答和分析缓存数据库 |
| `web/` | 暂无 | 通过 CLI、Notebook 和 JSON 查看结果；需要 UI 时再接入 Python 核心 |

## 明确的差异和边界

- Python 当前只支持 Claude SDK 兼容运行时与 stub；没有搬入 TS 的 OpenAI-compatible Workflow、自动降级与能力缓存。通用 Qwen 运行时在 P3 实现。
- Git 历史固定为深度 1，只提供该提交信息，不推断频繁修改文件。仓库概况暂不提供完整的目录/语言统计、TODO 扫描或 example manifests。
- 依赖发现是有上限的静态启发式。不会执行 import、解析所有构建别名、动态导入或证明调用关系；截断或无效 Python 源码可能无法解析。
- 工具发现的扫描、概况读取与正式 evidence batch 分别有上限；88,000 字节只指正式批次预算，不是所有扫描的总字节数。
- 引用校验验证路径来自已提供内容，不证明每句话正确。原始真实报告中的语义错误保留在实验记录中。
- 当前设计用于单次 CLI 或顺序实验。不同报告使用独立 runtime 和 run_id；不要并发共享同一个 runtime、RunStore 或同时写同一对象缓存。尚未交付多用户任务服务。
- 真实报告是改动前的固定历史基线。离线重验能验证结构与预算，不等于重新测量当前代码的模型行为或质量。

## 独立性与验证

配置、缓存和运行记录的路径规则见[配置与数据隔离](configuration.md)。从不同目录启动使用同一 Python 默认位置；CLI 与实验脚本共用设置加载器，现有 Python 结果继续复用。

默认 pytest 使用 `python/tests/fixtures/mini-repo`，它最初复制自 TS 基准样例。正常测试不再依赖根目录 fixture、Node 或 npm。包内 prompts、Skills、隐藏 plugin manifest 随 wheel 分发。

`scripts/ts_parity.mjs` 和 `test_ts_parity.py` 是历史迁移对照入口，保留但默认不运行。历史对照已验证同一 Git 内容下的预算、选择、Ledger 和拒绝类别，不代表全部功能或模型回答逐字等价。

`scripts/sync_assets.py` 仅是初次迁移工具，会覆盖包内资产；后续直接维护 Python 的 `assets/`，不要例行从 TS 同步覆盖。`assets/provenance.json` 按 LF 规范化后的文本字节计算 SHA-256，测试验证其与包内资产一致，避免 Windows 换行转换造成假差异。公共实验清单和种子在 `experiments/`；`artifacts/` 中的下载缓存、数据库与完整运行事件留在本地。
