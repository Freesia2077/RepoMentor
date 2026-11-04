# RepoMentor 运行记录

recipes.consume(iterator, n=None) 和指定 n 时分别怎样推进迭代器？剩余不足 n 项时会返回已消费元素的列表吗？

状态：success；新增模型请求：1；耗时：7.937 秒

当 n 为 None 时，consume 函数会一直调用 next() 直到迭代器抛出 StopIteration 异常，从而完全耗尽迭代器；当 n 为具体数值时，函数会尝试推进 n 步，若剩余项不足 n 项，则会将整个迭代器耗尽（抛出 StopIteration），但不会返回已消费元素的列表，仅返回迭代器本身供后续操作使用。

## 引用源码

### more_itertools/recipes.py:166-198

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
185: 
186:     If the iterator has fewer items remaining than the provided limit, the
187:     whole iterator will be consumed.
188: 
189:         >>> i = (x for x in range(3))
190:         >>> consume(i, 5)
191:         >>> next(i)
192:         Traceback (most recent call last):
193:           File "<stdin>", line 1, in <module>
194:         StopIteration
195: 
196:     """
197:     # Use functions that consume iterators at C speed.
198:     if n is None:
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
