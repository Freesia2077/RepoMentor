# RepoMentor 运行记录

那么 emit 遍历时为什么调用 slice？

状态：success；新增模型请求：2；耗时：12.171 秒

emit 方法调用 slice 是为了将事件处理器列表从 Map 中取出时，避免在遍历过程中修改原数组导致索引错乱或跳过元素，同时确保每次 emit 操作都是独立且安全的快照。

## 引用源码

### src/index.ts:106-107

```text
106: 				(handlers as EventHandlerList<Events[keyof Events]>)
107: 					.slice()
```

### src/index.ts:115-116

```text
115: 				(handlers as WildCardEventHandlerList<Events>)
116: 					.slice()
```

## 工具轨迹

| 工具 | 状态 | 来源位置 |
| --- | --- | --- |

## 质量边界

格式和来源校验不等于语义正确；以下仅为确定性诊断。

```json
{
  "version": "answer-diagnostics-v1",
  "issues": [],
  "semantic_correctness": "not_scored",
  "complete": null,
  "human_approved": false
}
```
