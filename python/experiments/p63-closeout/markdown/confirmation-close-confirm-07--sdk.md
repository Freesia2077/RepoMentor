# RepoMentor 运行记录

nth_or_last([10, 20], 8) 为什么返回 20？空输入且不提供 default 时的错误由哪个辅助函数产生？

状态：success；新增模型请求：2；耗时：18.016 秒

nth_or_last([10, 20], 8) 返回 20 是因为它先截取前 9 个元素，再取最后一个；空输入且无 default 时的错误由 last() 函数产生。

## 引用源码

### more_itertools/more.py:305-319

```text
305: def nth_or_last(iterable, n, default=_marker):
306:     """Return the nth or the last item of *iterable*,
307:     or *default* if *iterable* is empty.
308: 
309:         >>> nth_or_last([0, 1, 2, 3], 2)
310:         2
311:         >>> nth_or_last([0, 1], 2)
312:         1
313:         >>> nth_or_last([], 0, 'some default')
314:         'some default'
315: 
316:     If *default* is not provided and there are no items in the iterable,
317:     raise ``ValueError``.
318:     """
319:     return last(islice(iterable, n + 1), default=default)
```

### more_itertools/more.py:280-302

```text
280: def last(iterable, default=_marker):
281:     """Return the last item of *iterable*, or *default* if *iterable* is
282:     empty.
283: 
284:         >>> last([0, 1, 2, 3])
285:         3
286:         >>> last([], 'some default')
287:         'some default'
288: 
289:     If *default* is not provided and there are no items in the iterable,
290:     raise ``ValueError``.
291:     """
292:     try:
293:         if getattr(iterable, '__reversed__', None):
294:             return next(reversed(iterable))
295:         return deque(iterable, maxlen=1)[-1]
296:     except (IndexError, StopIteration):
297:         if default is _marker:
298:             raise ValueError(
299:                 'last() was called on an empty iterable, '
300:                 'and no default value was provided.'
301:             )
302:         return default
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
