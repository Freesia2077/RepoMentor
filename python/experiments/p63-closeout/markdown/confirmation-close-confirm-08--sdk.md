# RepoMentor 运行记录

recipes.consume(iterator, n=None) 和指定 n 时分别怎样推进迭代器？剩余不足 n 项时会返回已消费元素的列表吗？

状态：success；新增模型请求：2；耗时：11.313 秒

当 n 为 None 时，consume 函数会完全耗尽迭代器直到抛出 StopIteration；当 n 指定具体数值时，函数会推进迭代器 n 步，若剩余项不足 n 项则继续消耗完所有剩余项，但无论哪种情况都不会返回已消费元素的列表。

## 引用源码

### more_itertools/recipes.py:166-184

```text
166: def consume(iterator, n=None):
167:     """Advance *iterable* by *n* steps. If *n* is ``None``, consume it
168:     entirely.
169: 
170:     Efficiently exhausts an iterator without returning values. Defaults to
171:     consuming the whole iterator, but an optional second argument may be
172:     provided to limit consumption.
173: 
174:         >>> i = (x for x in range(10))
175:         >>> next(i)
176:         0
177:         >>> consume(i, 3)
178:         >>> next(i)
179:         4
180:         >>> consume(i)
181:         >>> next(i)
182:         Traceback (most recent call last):
183:           File "<stdin>", line 1, in <module>
184:         StopIteration
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
