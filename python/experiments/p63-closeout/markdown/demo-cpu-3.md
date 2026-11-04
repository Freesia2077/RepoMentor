# RepoMentor 运行记录

off 不传 handler 会怎样处理，是否删除 Map 的键？

状态：success；新增模型请求：2；耗时：8.468 秒

当调用 `off` 方法且不传 `handler` 参数时，mitt 会删除 Map 中对应事件类型的键，将其从事件注册表中移除。

## 引用源码

### src/index.ts:88-89

```text
88: 					all!.set(type, []);
89: 				}
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
