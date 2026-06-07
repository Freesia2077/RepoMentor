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

1. 理解包结构：查看 packages/ 或 apps/ 目录下的子包列表
2. 读取根配置：pnpm-workspace.yaml, lerna.json, turbo.json 等
3. 绘制依赖图：各 package.json 之间的相互引用关系
4. 找到共享包：shared/, common/, utils/ 等被多个包引用的模块
5. 了解 CI/CD：查看测试、构建、发布的流水线配置

## 常见架构模式

- 分层架构（applications → libraries → shared）
- 依赖注入（通过 workspace 协议引用）
- 统一构建（Turborepo/Nx 的任务编排）
