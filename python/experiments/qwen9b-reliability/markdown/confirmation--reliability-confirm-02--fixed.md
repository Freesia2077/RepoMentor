# RepoMentor 运行记录

mitt.on 支持 Symbol 事件名吗？实现是否转成字符串，仓库的对应测试如何验证保留 Symbol 键？

状态：success；新增模型请求：1；耗时：6.844 秒

mitt.on 支持 Symbol 事件名，实现中未进行字符串转换，直接以 Symbol 作为 Map 的键存储。仓库通过测试用例 `test/index_test.ts` 中的 `it('can take symbols for event types', ...)` 验证了 Symbol 键的保留。

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

### test/index_test.ts:92-96

```text
92: 		it('can take symbols for event types', () => {
93: 			const foo = () => {};
94: 			inst.on(eventType, foo);
95: 			expect(events.get(eventType)).to.deep.equal([foo]);
96: 		});
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
