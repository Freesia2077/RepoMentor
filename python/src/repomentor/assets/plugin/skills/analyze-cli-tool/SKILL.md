---
name: analyze-cli-tool
description: 分析 CLI 命令行工具类项目的策略模板
---

# CLI 工具分析策略

## 关注重点

- 命令行入口点与参数解析方式
- 命令（子命令）注册与路由机制
- CLI 与核心功能库的边界划分
- 输出格式化与日志级别控制
- 配置文件的读取（.rc 文件、环境变量等）

## 推荐探索路径

1. 从 repositoryProfile.entryCandidates 与清单路径中选择 CLI 入口候选
2. 用 `search_symbols` 调查 commander、yargs、argparse、command、subcommand 等概念
3. 用 `trace_module_dependencies` 从已索引入口确认命令分发到核心库的方向
4. 用 `find_related_tests` 为命令入口和核心实现寻找代表性测试
5. 输出一次 EvidencePlan，交由 Harness 在循环外批量读取源码

只使用当前 Skill Policy 暴露的 RepoMentor MCP 工具；不得调用原始文件、Shell、网络或编辑工具。

## 常见架构模式

- 命令模式（每个子命令独立处理）
- "薄 CLI + 胖核心" 分层（CLI 只负责解析和格式化）
- 管道模式（多个处理步骤串联）
