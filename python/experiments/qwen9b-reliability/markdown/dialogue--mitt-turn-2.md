# RepoMentor 运行记录

那么 off('FOO') 不传 handler，是删除 Map 的键还是清空对应数组？

状态：success；新增模型请求：2；耗时：29.719 秒

否，mitt 的 on 方法不会把事件名转成小写，它严格保持事件名的大小写。

## 引用源码

### test/index_test.ts:79-90

```text
79: 		it('should NOT normalize case', () => {
80: 			const foo = () => {};
81: 			inst.on('FOO', foo);
82: 			inst.on('Bar', foo);
83: 			inst.on('baz:baT!', foo);
84: 
85: 			expect(events.get('FOO')).to.deep.equal([foo]);
86: 			expect(events.has('foo')).to.equal(false);
87: 			expect(events.get('Bar')).to.deep.equal([foo]);
88: 			expect(events.has('bar')).to.equal(false);
89: 			expect(events.get('baz:baT!')).to.deep.equal([foo]);
90: 		});
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
