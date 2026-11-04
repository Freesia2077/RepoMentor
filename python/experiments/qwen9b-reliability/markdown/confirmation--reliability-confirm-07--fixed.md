# RepoMentor 运行记录

first_true([0, False, 2], default='missing') 返回什么？如果 pred=lambda x: x == 0，返回的 0 会因为是假值而被丢弃吗？结合实现解释。

状态：success；新增模型请求：1；耗时：5.453 秒

first_true([0, False, 2], default='missing') 返回 0。传入的 pred 默认为 None，此时 filter 使用内置 bool 函数进行判断，0 被视为真值（True），因此不会被丢弃，而是作为第一个匹配项被返回。

## 引用源码

### more_itertools/recipes.py:571-588

```text
571: def first_true(iterable, default=None, pred=None):
572:     """
573:     Returns the first true value in the iterable.
574: 
575:     If no true value is found, returns *default*
576: 
577:     If *pred* is not None, returns the first item for which
578:     ``pred(item) == True`` .
579: 
580:         >>> first_true(range(10))
581:         1
582:         >>> first_true(range(10), pred=lambda x: x > 5)
583:         6
584:         >>> first_true(range(10), default='missing', pred=lambda x: x > 9)
585:         'missing'
586: 
587:     """
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
