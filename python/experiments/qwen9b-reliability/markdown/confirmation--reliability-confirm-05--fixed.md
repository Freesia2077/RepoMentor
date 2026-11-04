# RepoMentor 运行记录

Scheduler.jobs 非空但 get_next_run(tag) 找不到匹配标签时，会返回所有任务中最早的时间吗？如何复用 get_jobs，匹配成功后怎样选择？

状态：success；新增模型请求：1；耗时：6.11 秒

当 `Scheduler.jobs` 非空但 `get_next_run(tag)` 找不到匹配标签时，会返回 `None` 而非最早时间；复用 `get_jobs` 匹配成功后，应通过 `min(jobs).next_run` 选择最早时间。

## 引用源码

### schedule/__init__.py:188-192

```text
188:         if not self.jobs:
189:             return None
190:         jobs_filtered = self.get_jobs(tag)
191:         if not jobs_filtered:
192:             return None
```

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
