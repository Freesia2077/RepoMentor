# RepoMentor 运行记录

mitt 的 on 返回 emitter 支持链式调用吗？结合方法体说明注册后的返回值。

状态：success；新增模型请求：2；耗时：11.593 秒

mitt 的 on 方法不支持链式调用，其注册后的返回值是 void，无法直接用于后续方法调用。

## 引用源码

### src/index.ts:26-26

```text
26: 	on<Key extends keyof Events>(type: Key, handler: Handler<Events[Key]>): void;
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
