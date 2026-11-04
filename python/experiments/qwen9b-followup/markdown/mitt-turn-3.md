# RepoMentor 运行记录

切换到 emit，普通 handler 和通配符 handler 的参数、调用顺序分别是什么？

状态：success；新增模型请求：2；耗时：19.516 秒

普通 handler 只接收事件数据 `evt`，通配符 handler 接收事件类型 `type` 和事件数据 `evt`；调用顺序是先执行普通 handler，再执行通配符 handler。

## 引用源码

### src/index.ts:109-109

```text
109: 						handler(evt!);
```

### src/index.ts:118-118

```text
118: 						handler(type, evt!);
```

### src/index.ts:107-110

```text
107: 					.slice()
108: 					.map((handler) => {
109: 						handler(evt!);
110: 					});
```

### src/index.ts:113-120

```text
113: 			handlers = all!.get('*');
114: 			if (handlers) {
115: 				(handlers as WildCardEventHandlerList<Events>)
116: 					.slice()
117: 					.map((handler) => {
118: 						handler(type, evt!);
119: 					});
120: 			}
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
