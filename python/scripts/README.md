# Python 脚本入口

当前问答和实验通过 `python.exe -s -m repomentor.cli` 或 `run.ps1` 进入。Agent 循环始终由 SDK Runner 执行；这里的脚本不构成第二套活动引擎。

| 日常工作 | 活动入口 |
| --- | --- |
| 问答、持续追问、报告 | CLI 的 `ask`、`chat`、`report` |
| 新实验预检、运行、复核 | CLI 的 `benchmark prepare/run/summary/review/verify/finalize` |
| 查询题库与校验身份 | CLI 的 `questions` |
| 检查封存原件 | `verify_archives.py`；包含工作流诊断产物的完整性检查 |
| 本地模型服务 | `ollama_local.ps1`；不随离线检查自动启动 |
| 环境核查与依赖锁 | `audit_environment.py`、`audit_future_dependencies.py`、`lock_environment.py` |
| 准备已选神经检索权重 | `prepare_retrieval_models.py`；日常 BM25 不需要 |

`p63*`、`p6*`、`run_p*`、`audit_p*`、`qwen9b*`、`rag_interview*`、`rag_expansion.py`、`rag_unified.py` 等保留为历史批次的方法与复核入口。它们对应各自冻结的输入、实现和资源身份，不作为新实验模板。相互导入及既有路径保留，避免搬动脚本破坏历史回放。

10 月 23 日的一次性设计、复核、统计、演示脚本及诊断/schema 回放代码已归入[方法源码包](../experiments/improvement-study/method-source.zip)，不继续维护为活动入口。查看结果使用既有 `inspect-run`、报告文件和统一归档核验，不再维护重复的导出器。

历史生成和整理脚本的结果以 [实验索引](../notes/experiment-index.md) 为准；检查旧结果优先使用归档校验。旧脚本中存在的试验条件不代表当前默认支持，旧协议验证也不代表当前答案正确率。

本机所有 Python 操作使用 `D:\anaconda\envs\repomentor\python.exe -s`，不新建环境。完整离线检查：

```powershell
.\run.ps1 -m pytest -q -p no:cacheprovider
.\run.ps1 -m ruff check --no-cache src scripts tests
.\run.ps1 scripts/verify_archives.py
```
