# RepoMentor 前端设计文档

## 概览
- **目标**：为 RepoMentor 后端构建一个无需登录的轻量级 Web 仪表盘。用户输入 GitHub 仓库 URL，实时观察分析进度，最终在一个单页中浏览涵盖三个阶段的结构化分析报告。
- **技术栈**：Vite + React + TypeScript + Vanilla CSS
- **项目位置**：`web/` 目录（采用 Monorepo 结构，与后端共存）。
- **设计调性**：Anthropic 风格 —— 米白色纸张感背景、温暖的深灰黑文字、少量陶土橙作为强调色，大量留白与克制的分隔线，通过高质量的排版而非视觉特效建立层级关系。

## 页面结构（单页应用，双视图状态）

### 视图 1：输入状态
- 居中布局。顶部为品牌标识 + 一句产品 Slogan。
- 核心区包含一个输入框（GitHub URL）+ 分支选择（默认 `main`）+ 提交按钮。
- 底部区域预留给曾经分析过的仓库列表（未来仪表盘功能的入口）。

### 视图 2：分析/报告状态
- 顶部为固定导航栏，包含锚点链接：Overview（概览） → Architecture（架构） → Contribute（贡献）。
- **分析进行中**：展示三个阶段的卡片（Explorer / Mentor / Contributor），每个卡片动态显示当前状态（`pending` → `running` → `done`）。正在运行的阶段下方会显示简短的进度文字。底部有一个可折叠的「详细日志」区域，展开后可查看完整的 SSE 事件流。已完成的阶段将渐次展开其结果内容。
- **分析完成**：三个内容区域从上到下依次平铺展示：
  1. **Overview（Explorer）**：项目摘要、技术栈标签、模块地图、入口文件列表。
  2. **Architecture（Mentor）**：架构概览（支持 Markdown 渲染）、依赖图、推荐阅读路径（步骤列表）、关键设计模式、代码规范。
  3. **Contribute（Contributor）**：Good First Issues 卡片组、环境搭建指南、入口文件说明、新人注意事项。
- **交互式问答**：当后端 SSE 推送 `interact:ask` 事件时，当前正在运行的阶段卡片下方会展开问答区域（选项按钮组）。用户点击选项后，系统通过 `/analysis/:id/ask` 接口提交答案。如果超时，该区域会自动折叠。

## 核心组件拆分

| 组件名 | 职责描述 |
|-----------|----------------|
| `App` | 路由与全局状态管理，维护 SSE 连接的生命周期 |
| `InputView` | URL 输入、分支选择以及分析任务提交 |
| `AnalysisView` | 作为进度展示与最终报告渲染的容器 |
| `StageIndicator` | 三阶段进度条及状态卡片 |
| `LogPanel` | 可折叠的 SSE 实时日志流 |
| `InteractionPrompt` | 处理 `askUser` 交互式问答的弹出组件 |
| `ExplorerSection` | Explorer 阶段结果的视觉渲染 |
| `MentorSection` | Mentor 阶段结果的视觉渲染（包含 Markdown 解析） |
| `ContributorSection` | Contributor 阶段结果的视觉渲染 |
| `AnchorNav` | 固定在顶部的锚点导航条 |

## 数据流向
```
用户输入 URL → POST /analysis → 获取 taskId
                                    ↓
                        GET /analysis/:id/stream (建立 SSE 长连接)
                                    ↓
               收到 stage:start 事件 → 更新 StageIndicator 状态
               收到 stage:progress 事件 → 更新进度文字 + 写入 LogPanel
               收到 interact:ask 事件 → 弹出 InteractionPrompt 让用户选择
                                    ↓
               收到 stage:done 事件 → 渲染对应阶段的 Section 结果
               收到 task:completed 事件 → 平滑切换至完整报告视图
```

## 设计语言与视觉规范
- **背景色**：`#FAF8F5`（米白色，带纸张质感）
- **文字色**：`#2D2D2D`（温暖的深灰黑，非纯黑）
- **强调色**：`#C2603E`（陶土橙，用于按钮、关键链接、高亮状态）
- **分隔线**：克制的 `1px solid #E8E4DF`，避免使用多余的阴影。
- **字体方案**：正文阅读使用衬线体 Literata，标题与标签使用无衬线体 Instrument Sans。
- **动效处理**：仅在状态转换时使用（例如某个阶段完成后，内容容器平滑展开），全程使用 CSS transition，绝对避免弹跳或闪烁。
