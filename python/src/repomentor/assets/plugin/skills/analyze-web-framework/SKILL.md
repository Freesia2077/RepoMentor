---
name: analyze-web-framework
description: 分析 Web 框架类开源项目的策略模板
---

# Web 框架分析策略

## 关注重点

- 路由定义方式与请求生命周期
- 中间件/插件机制的设计模式
- 配置加载与启动流程
- 模板引擎和响应渲染机制
- 扩展点：如何添加自定义中间件/插件

## 推荐探索路径

1. 从 repositoryProfile.entryCandidates 与清单路径中选择框架入口候选
2. 用 `search_symbols` 调查 router、middleware、plugin、handler 等概念
3. 用 `trace_module_dependencies` 从索引中的入口确认请求生命周期依赖方向
4. 用 `find_related_tests` 为路由、中间件或插件候选寻找代表性测试
5. 输出一次 EvidencePlan，由 Harness 在循环外读取并裁剪真实证据

只使用当前 Skill Policy 暴露的 RepoMentor MCP 工具；不得请求 Read、Grep、Glob、Bash、网络或编辑能力。

## 常见架构模式

- 洋葱模型（中间件层层包裹）
- 工厂模式创建应用实例
- 观察者模式用于生命周期钩子
