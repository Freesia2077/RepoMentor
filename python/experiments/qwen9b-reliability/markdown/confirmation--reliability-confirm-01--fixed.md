# RepoMentor 运行记录

mitt 的普通事件 handler 在执行时再 on 同类型的新 handler，新 handler 会在这一次 emit 中执行吗？结合注册与派发的实现说明。

状态：success；新增模型请求：1；耗时：3.985 秒

不会执行。

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
