# RepoMentor 运行记录

mitt 的 on 返回 emitter 支持链式调用吗？结合方法体说明注册后的返回值。

状态：success；新增模型请求：1；耗时：8.531 秒

mitt 的 on 方法在注册事件处理器后返回 void，不支持链式调用。

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

### src/index.ts:26-27

```text
26: 	on<Key extends keyof Events>(type: Key, handler: Handler<Events[Key]>): void;
27: 	on(type: '*', handler: WildcardHandler<Events>): void;
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
