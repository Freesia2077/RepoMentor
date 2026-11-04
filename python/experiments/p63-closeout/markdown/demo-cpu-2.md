# RepoMentor 运行记录

那么测试如何验证大小写保持不变？

状态：success；新增模型请求：2；耗时：13.39 秒

测试通过直接传入不同大小写的字符串（如 'foo' 和 'FOO'）作为事件类型，并断言 `inst.on()` 注册后，`all` Map 中存储的键值完全一致，从而验证 mitt 不会统一转换事件名大小写。

## 引用源码

### src/index.ts:66-72

```text
66: 		on<Key extends keyof Events>(type: Key, handler: GenericEventHandler) {
67: 			const handlers: Array<GenericEventHandler> | undefined = all!.get(type);
68: 			if (handlers) {
69: 				handlers.push(handler);
70: 			} else {
71: 				all!.set(type, [handler] as EventHandlerList<Events[keyof Events]>);
72: 			}
```

### test/index_test.ts:27-36

```text
27: 	type Events = {
28: 		foo: unknown;
29: 		constructor: unknown;
30: 		FOO: unknown;
31: 		bar: unknown;
32: 		Bar: unknown;
33: 		'baz:bat!': unknown;
34: 		'baz:baT!': unknown;
35: 		Foo: unknown;
36: 		[eventType]: unknown;
```

### test/index_test.ts:56-61

```text
56: 		it('should register handler for new type', () => {
57: 			const foo = () => {};
58: 			inst.on('foo', foo);
59: 
60: 			expect(events.get('foo')).to.deep.equal([foo]);
61: 		});
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
