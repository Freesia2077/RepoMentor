# 配置与数据

本机固定复用 `D:\anaconda\envs\repomentor\python.exe -s` 或 `run.ps1`，不另建环境。

## 配置选择

`ask/chat`、`report --runtime sdk/local` 共用已有 `load_chat_settings`，按下述顺序选择配置；没有覆盖时读取 Python 的 `config/model-settings.json`。离线查看和 stub 无需模型设置。Claude 兼容入口需要自己的 Anthropic-compatible profile。

通用加载顺序：调用者文件 → `REPOMENTOR_PY_SETTINGS` → 完整 `REPOMENTOR_PY_*` 模型环境变量组 → Python 默认文件。选择完整来源，不拼接字段；无效配置报错。路径环境变量必须为绝对路径。不读取 TS 配置、不自动加载 `.env`，不从共享 `ANTHROPIC_*` / `OPENAI_API_KEY` 获取应用配置。

`ChatSettings` 记录 backend、服务版本、模型 revision、量化、总窗口、输出上限、采样和 tokenizer 身份。`presence_penalty` 是可选参数，显式设置后在固定 RAG 与 SDK 中均传到后端；未设置时省略，保持旧 profile 身份。不要输出或提交真实 key。

执行路径与模型配置分别选择：新建 `ask/chat` 和 `report` 默认SDK，`--engine rag` 选择固定问答对照；已存在的聊天会话沿用保存引擎。两条问答路径共用代码增强取证，SDK在此基础上由Runner补查。`--retriever`选择检索器，`--source-bytes`限制初始源码，均不修改模型总窗口。更改默认入口不代表回答质量通过验收。

## 日常与校准配置

日常 profile 为既有 Qwen3.5-9B Q4_K_M，关闭思考、温度0 / top_p=1。各项上限的作用与单位不同：

| 项目 | 配置位置 | 当前日常值与含义 |
| --- | --- | --- |
| 模型单次总窗口 | profile `context_window` | 16,384 tokens；输入、工具/schema、模板余量和输出共同使用 |
| 最终回答输出 | profile `max_tokens` / `--answer-max-tokens` | 最多2,048 tokens；属于总窗口的一部分 |
| SDK单次研究输出 | `SdkAgentEngine` | 最多1,536 tokens，且不超过profile输出上限 |
| 初始检索源码 | `RetrievalPolicy.source_bytes` / `--source-bytes` | 最多8,000 UTF-8字节、6个检索单元；不是8K tokens |
| SDK累计交付源码 | `AgentLimits.max_source_bytes` | 默认36,000字节；跨工具调用累计，不是单次模型窗口 |

问答最终输出遵循profile，报告最终输出默认2,048 tokens；`--answer-max-tokens`显式覆盖二者。SDK日常问答默认最多8次决策、6次工具、300秒。冻结实验可单独限制为4次决策或其他预算，实际值见各自dataset的 `protocol.settings`、`policies` 和 `protocol.limits`；不回写日常profile。通用 `ChatSettings` 类的缺省值也不能代替本机显式profile。未因单个开发案例推广新配置。

日常配置与历史校准实验分别记录，不把开发诊断中的采样、思考或大窗口配置推广为默认。客户端窗口、服务模型别名与digest须一致；模板、工具/schema、输入和输出共同计入预算。非思考路径使用固定tokenizer并核验身份，历史结果从[实验索引](experiment-index.md)查询。

## 检索与部署

默认 `syntax / bm25 / cpu`，代码增强开启。神经路径显式指定 `--retriever hybrid-rerank --retrieval-device cuda`，从本地权重加载；检索配置为 `config/model-settings.retrieval.json`，可用 `--retrieval-settings` 或 `REPOMENTOR_PY_RETRIEVAL_SETTINGS` 覆盖。

本机便携 Ollama 0.34.3，回环端口11435、单并发，复用已有模型层。运行与停止：

```powershell
.\scripts\ollama_local.ps1 status
.\scripts\ollama_local.ps1 start
.\scripts\ollama_local.ps1 stop
```

启动器按父PID和完整程序路径管理本项目 runner；`LLAMA_ARG_FIT_TARGET=2048` 是显存余量，不是 token 上限。本机 RTX4060 Laptop 8GB 的速度和offload层数受当时资源占用影响，不把不同运行条件的耗时用于公平排名。新机器安装和权重准备入口见[scripts 索引](../scripts/README.md)，本机已有环境无需重复下载。

## 保存与协议

`REPOMENTOR_PY_DATA_DIR` 管独立私有数据根；`--data-dir` 只覆盖该 CLI 的运行目录，不自动迁移模型、快照或缓存。私有数据包含 runtime 会话/索引、corpus 快照、models、tokenizers、服务PID/日志。公开实验位于 `experiments/`，属于结果证据。

SDK 使用显式兼容客户端，关闭 tracing、自动 HTTP 重试和代理继承。六次工具额度统计仓库操作，答案提交另记且不绕过八次模型预算。[Ollama 0.34.3 官方兼容清单](https://github.com/ollama/ollama/blob/v0.34.3/docs/api/openai-compatibility.mdx)未承诺支持 `tool_choice`；预算耗尽由 SDK 回调停止工具循环，不依赖服务遵从此字段。Claude 兼容设置只映射到子进程环境；这不是操作系统沙箱。发送中途失败不自动重发，已发送或不确定 dispatch 都计入对应账本。

模型/快照/配置/输入/实现身份变化后新建会话。旧批次通过离线归档命令核验；不要改身份字段强行复用。凭据、模型设置、权重、源码缓存和运行数据库不提交。
