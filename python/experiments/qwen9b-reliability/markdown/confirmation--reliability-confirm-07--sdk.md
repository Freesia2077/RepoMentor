# RepoMentor 运行记录

first_true([0, False, 2], default='missing') 返回什么？如果 pred=lambda x: x == 0，返回的 0 会因为是假值而被丢弃吗？结合实现解释。

状态：success；新增模型请求：2；耗时：45.203 秒

first_true([0, False, 2], default='missing') 返回 2；若 pred=lambda x: x == 0，则返回 0 且不会被丢弃。

## 引用源码

### more_itertools/recipes.py:588-588

```text
588:     return next(filter(pred, iterable), default)
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
