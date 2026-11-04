# RepoMentor Python

面向开发者的开源仓库学习助手，提供架构导览、源码阅读路线、函数与测试解析、多轮问答和源码引用。Python独立运行，原TypeScript应用保留。

当前使用共享代码检索与OpenAI Agents SDK按需取证，harness管理可见源码、工具预算、答案提交和恢复。公开问答结果见[SWE-QA 50题五组评测](experiments/sweqa-50-2026-11-03/README.md)；这是模型辅助评分，学习效率和线上收益尚未独立测量。

## 使用

本机沿用`D:\anaconda\envs\repomentor\python.exe -s`，由`run.ps1`封装。从本目录执行：

```powershell
.\run.ps1 -m repomentor.cli doctor
.\scripts\ollama_local.ps1 start
.\run.ps1 -m repomentor.cli snapshot https://github.com/jd/tenacity.git --revision 32ac68f0941319c0d2f4fb927f96e4ee29bb46c6
.\run.ps1 -m repomentor.cli chat --snapshot-id bcb069b93214c1a0a2f7e705
```

模型使用Python独立的`config/model-settings.json`，配置及依赖见[配置说明](notes/configuration.md)。聊天用`/exit`退出、`/resume`恢复未完成轮次，`chat --session ID`继续会话。

新建问答默认代码增强BM25/CPU与SDK Agent；`--engine rag`选择单次检索对照，`--retriever hybrid-rerank --retrieval-device cuda`显式使用混合重排，需要本地检索权重。已有会话固定原引擎；Python不依赖Node、TS服务或前端，不执行目标仓库代码。

## 离线演示与检查

```powershell
.\run.ps1 -m repomentor.cli inspect-run experiments/sweqa-50-2026-11-03/combined/results.jsonl --case-id sweqa-flask-042--hybrid_rerank
.\run.ps1 scripts/verify_archives.py
.\run.ps1 -m repomentor.cli questions verify
.\run.ps1 -m pytest -q -p no:cacheprovider
.\run.ps1 -m ruff check --no-cache src scripts tests
```

`inspect-run`仅展示保存答案、来源与有限轨迹，不调用模型，可用`--output PATH`导出Markdown。无引用或失败时仍可查看保存原文；展示成功不代表答案正确。完整演示见[面试稿](notes/interview-playbook.md)。

## 文档入口

| 需要了解什么 | 阅读入口 |
| --- | --- |
| 当前功能、默认路径、效果与限制 | [项目文档](项目文档.md) |
| 模块职责与Agent harness | [架构](notes/architecture.md) |
| 环境、模型和运行参数 | [配置](notes/configuration.md)、[脚本入口](scripts/README.md) |
| 数据划分与评测规则 | [数据协议](notes/data-protocol.md) |
| 评测原件与关键案例 | [实验索引](notes/experiment-index.md) |
| 简历、项目讲述与技术追问 | [简历](notes/resume-project.md)、[面试稿](notes/interview-playbook.md)、[技术问答](notes/rag-interview-assessment.md) |
| 收尾状态与第二项目 | [唯一计划](notes/repomentor-python-and-minimind-plan.md) |

模型设置、凭据、权重、源码缓存和数据库不提交。后训练尚未启动。
