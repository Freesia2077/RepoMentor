# RepoMentor 运行记录

mitt 的 on 会统一转换事件名大小写吗？

状态：success；新增模型请求：2；耗时：13.39 秒

mitt 的 on 方法不会统一转换事件名的大小写，它直接接收并存储传入的字符串或符号作为事件类型。

## 引用源码

### src/index.ts:66-73

```text
66: 		on<Key extends keyof Events>(type: Key, handler: GenericEventHandler) {
67: 			const handlers: Array<GenericEventHandler> | undefined = all!.get(type);
68: 			if (handlers) {
69: 				handlers.push(handler);
70: 			} else {
71: 				all!.set(type, [handler] as EventHandlerList<Events[keyof Events]>);
72: 			}
73: 		},
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
