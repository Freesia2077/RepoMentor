# RepoMentor 运行记录

repeat(job, *args, **kwargs) 创建的装饰器返回原始函数还是新的执行包装器？如何登记任务与附加参数？

状态：success；新增模型请求：1；耗时：8.297 秒

repeat 装饰器返回原始函数，而非新的执行包装器；任务登记通过装饰器内部调用 job.do() 将原始函数及其附加参数注册到调度器，但实际执行仍由原始函数完成。

## 引用源码

### schedule/__init__.py:909-911

```text
909:     def _schedule_decorator(decorated_function):
910:         job.do(decorated_function, *args, **kwargs)
911:         return decorated_function
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
