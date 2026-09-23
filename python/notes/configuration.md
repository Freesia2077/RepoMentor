# 配置与数据隔离

Python 与原 TS Web 应用共处同一 Git 仓库，但应用配置、数据库、下载缓存和 SDK 会话目录分别归属。前端未重写，Python 核心不依赖 TS 启动。

## 模型配置

`load_model_settings()` 使用以下顺序选择**一个完整来源**：

1. 调用者提供的文件，例如 CLI `--settings`。
2. `REPOMENTOR_PY_SETTINGS` 指定的绝对文件路径。
3. 专属环境变量组：`REPOMENTOR_PY_API_KEY`、`REPOMENTOR_PY_BASE_URL`、`REPOMENTOR_PY_MODEL`，可选 `REPOMENTOR_PY_PROVIDER`。
4. Python 主目录下的 `config/model-settings.json`。

环境变量组一旦出现任意字段，就必须完整提供必填项；不从默认文件借用密钥或端点。显式文件无效时也不回退。文件支持 `provider`、`apiKey`、`baseUrl`、`model`、`timeout_seconds`、`max_turns`、`max_budget_usd`，完整空模板在 `config/model-settings.example.json`。

Python 不自动搜寻当前目录、父目录、目标仓库的 `.env` 或 TS 设置。运行时不读取或修改 TS 配置，也不自动迁移；用户明确要求时，可一次性复制到 Python 本地配置，此后分别维护。真实调用前需要填写 Python 独立配置；stub、已有报告复核及缓存命中的实验不需要凭据。

## 路径

`paths.py` 集中解析位置。源码/可编辑安装默认主目录是 Python 包所在的 `python/`；独立 wheel 安装默认 `~/.repomentor-python/`。`REPOMENTOR_PY_HOME` 可指定专属主目录。

`REPOMENTOR_PY_DATA_DIR` 覆盖私有数据根目录（默认主目录下 `artifacts/`）。CLI 使用其 `runtime/`；实验使用 `corpus/` 和 `live/`；SDK 临时 cwd 与 `CLAUDE_CONFIG_DIR` 使用 `sdk-tmp/session-*`，会话结束删除。`--data-dir` 仅覆盖当前 CLI 的快照、数据库和报告目录，不改变实验缓存或 SDK 临时根目录。

路径类环境变量要求绝对路径，以免根目录启动、Notebook 和 CLI 使用不同数据。显式 CLI 路径参数允许相对当前工作目录。公开实验清单和已保存样例继续位于 `python/experiments/`，属于可版本化记录，不能当作私有运行目录。

默认目录保持与既有 Python 产物兼容，没有重新下载语料或重跑历史模型报告。`freeze_corpus.py --legacy` 也只使用 Python 已审计的 manifest，不再从 TS benchmark 创建回退数据。

## SDK 协议环境

官方 SDK 子进程仍需要 `ANTHROPIC_*` 参数，Python adapter 从已选定的 `ModelSettings` 显式生成这些参数。SDK 会合并父进程环境，因此 adapter 在子进程覆盖继承的 `ANTHROPIC_*` 模型/认证/请求头设置，并关闭外部 Bedrock、Vertex、Foundry 路由开关；父进程环境不修改。操作系统的 PATH、代理等通用环境仍正常继承。这是配置隔离，不是操作系统沙箱。

SDK 的 cwd、配置目录和 MCP 配置与之前一样受控，不加载目标仓库的设置。日志只输出脱敏后的配置；配置无效会在克隆仓库前失败。单次调用超时默认使用 Python 模型配置中的值，只有 `--call-timeout` 显式覆盖。

## 维护约定

真实模型配置被 `.gitignore` 排除，公开模板不含凭据。后续 CLI、Notebook 和实验入口应复用 `config.py` 与 `paths.py`，不要重新拼接 TS 路径或从共享模型变量读取配置。历史 TS 对照和资产迁移脚本仍是显式、非日常入口。

## 本轮验证（2026-09-23）

- 指定 Conda 环境下，完整 Python 测试 57 项通过，1 项历史 TS 对照未选中；异常脱敏修正后另复跑配置测试 9 项通过。
- 覆盖配置来源优先级、不混用凭据、无 TS/通用环境回退、从不同 cwd 启动、独立数据目录、SDK 参数映射、缺配置时不克隆，以及保留文件指定的超时。
- 从仓库根目录执行 doctor 和真实 CLI stub 流程，产物位于 `python/artifacts/runtime/`；mitt 历史报告复用命中，0 次新增模型 API 调用。
- Ruff 检查与格式检查通过。未读取或修改真实 TS 配置，未启动 TS 前后端。
