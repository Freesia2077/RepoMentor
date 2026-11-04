# RepoMentor 运行记录

mitt.emit 触发未注册的事件类型时，会创建该类型的 Map 键吗？通配符处理器还会收到什么参数？

状态：success；新增模型请求：2；耗时：16.25 秒

mitt.emit 触发未注册的事件类型时，不会创建该类型的 Map 键；通配符处理器只会收到事件类型（type）和事件数据（evt）两个参数，不会收到事件类型作为第二个参数。

## 引用源码

### src/index.ts:103-121

```text
103: 		emit<Key extends keyof Events>(type: Key, evt?: Events[Key]) {
104: 			let handlers = all!.get(type);
105: 			if (handlers) {
106: 				(handlers as EventHandlerList<Events[keyof Events]>)
107: 					.slice()
108: 					.map((handler) => {
109: 						handler(evt!);
110: 					});
111: 			}
112: 
113: 			handlers = all!.get('*');
114: 			if (handlers) {
115: 				(handlers as WildCardEventHandlerList<Events>)
116: 					.slice()
117: 					.map((handler) => {
118: 						handler(type, evt!);
119: 					});
120: 			}
121: 		}
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
