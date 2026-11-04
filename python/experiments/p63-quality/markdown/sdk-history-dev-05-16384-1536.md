# RepoMentor 运行记录

get_jobs() 不传 tag 返回内部列表本身吗？

状态：success；新增模型请求：2；耗时：8.688 秒

是的，当 `get_jobs()` 不传 `tag` 参数（即默认为 `None`）时，它返回的是内部 `self.jobs` 列表的浅拷贝，而非列表本身。

## 引用源码

### schedule/__init__.py:122-133

```text
122:     def get_jobs(self, tag: Optional[Hashable] = None) -> List["Job"]:
123:         """
124:         Gets scheduled jobs marked with the given tag, or all jobs
125:         if tag is omitted.
126: 
127:         :param tag: An identifier used to identify a subset of
128:                     jobs to retrieve
129:         """
130:         if tag is None:
131:             return self.jobs[:]
132:         else:
133:             return [job for job in self.jobs if tag in job.tags]
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
