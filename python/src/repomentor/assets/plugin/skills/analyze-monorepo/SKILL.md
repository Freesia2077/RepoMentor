---
name: analyze-monorepo
description: 分析 Monorepo 多包管理类项目的策略模板
---

# Monorepo 分析策略

## 关注重点

- 包之间的依赖关系图
- 共享配置与工具链
- 包发布流程与版本管理
- 工作区（workspaces）组织方式
- 构建系统与任务编排

## 推荐探索路径

1. 从 repositoryProfile 的目录统计和工程文件路径识别 workspace 与包边界
2. 用 `search_symbols` 调查 workspace、shared、common 或核心包概念
3. 用 `trace_module_dependencies` 从索引中的包入口确认跨包依赖方向
4. 用 `find_related_tests` 为代表性包入口定位测试路径
5. 输出一次 EvidencePlan，由 Harness 统一读取根配置、包入口和测试证据

不得自行读取目录或配置文件；只可使用 RepoMentor MCP 返回的路径和统计摘要。

## 常见架构模式

- 分层架构（applications → libraries → shared）
- 依赖注入（通过 workspace 协议引用）
- 统一构建（Turborepo/Nx 的任务编排）
