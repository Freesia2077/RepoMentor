# Python 辅助脚本

问答和实验的正式入口为`python -s -m repomentor.cli`或`run.ps1`：`ask/chat/report`运行助手，`benchmark`准备、执行和复核实验，`questions`管理题库。后训练使用独立的`python -s -m sftgrpo.prepare`与`python -s -m sftgrpo.train`。

本目录保留下列辅助工具：

| 文件 | 用途 |
| --- | --- |
| `ollama_local.ps1`、`install_ollama.ps1` | 管理或安装本地模型服务 |
| `prepare_qwen9b.py` | 准备RAG的9B模型配置与tokenizer，复用已核验资产 |
| `prepare_retrieval_models.py` | 准备向量检索与重排权重 |
| `audit_environment.py`、`lock_environment.py` | 核对环境和记录依赖版本 |
| `freeze_corpus.py` | 固定语料仓库版本、划分与许可证来源 |
| `sync_assets.py` | 同步受信的项目提示资产 |
| `verify_archives.py` | 离线校验封存实验的文件哈希 |
| `rag_interview.py`、`rag_interview_delivery.py`、`rag_unified.py` | 旧评测的取证与请求账本实现；现有回归测试仍检查其边界行为 |
| `ts_parity.mjs` | 显式启用的历史TS对照测试入口 |

早期`p63*`、`run_p*`、`audit_p*`及一次性诊断、整理脚本已从工作树移除，原实现可通过Git历史查阅。保留的评测与兼容数据见[实验目录](../experiments/README.md)，其余历史原件已本地封存；新实验使用统一CLI。

从Python项目根目录运行离线检查：

```powershell
.\run.ps1 -m pytest -q -p no:cacheprovider
.\run.ps1 -m ruff check --no-cache src scripts tests
.\run.ps1 scripts/verify_archives.py
```

模型服务、下载和真实推理需要显式运行相应命令，离线检查不会自动启动这些操作。
