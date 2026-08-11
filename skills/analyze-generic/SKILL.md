---
name: analyze-generic
description: 通用项目分析策略（兜底模板，适用于任何类型的项目）
---

# 通用项目分析策略

## 关注重点

- 项目入口文件与启动流程
- 顶层目录结构反映的模块划分
- 依赖关系（重点关注核心依赖）
- README 中提到的关键概念
- 构建、测试、部署相关配置

## 推荐探索路径

1. 从 repositoryProfile 的入口、测试与工程文件索引形成候选集合
2. 用 `search_symbols` 定位一个关键概念，只消费返回的路径与统计摘要
3. 用 `trace_module_dependencies` 从索引中的入口候选确认内部依赖方向
4. 用 `find_related_tests` 为核心候选寻找代表性测试路径
5. 输出一次 EvidencePlan；源码内容由 Harness 在 Agent 循环外统一读取

不得请求或假设存在 Read、Glob、Grep、Bash、网络或编辑工具。工具参数中的路径必须逐字来自 repositoryProfile.fileIndex。

## 常见架构模式

由于项目类型未知，请根据实际代码特征自行归纳架构模式，重点关注：
- 模块之间的依赖方向（是否单向？是否有循环？）
- 核心抽象（接口/基类）被哪些模块实现/继承
- 数据流向（输入 → 处理 → 输出 的路径）
