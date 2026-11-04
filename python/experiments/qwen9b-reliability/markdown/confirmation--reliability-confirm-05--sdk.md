# RepoMentor 运行记录

Scheduler.jobs 非空但 get_next_run(tag) 找不到匹配标签时，会返回所有任务中最早的时间吗？如何复用 get_jobs，匹配成功后怎样选择？

状态：success；新增模型请求：2；耗时：21.484 秒

当 Scheduler.jobs 非空但 get_next_run(tag) 找不到匹配标签时，不会返回所有任务中最早的时间，而是返回 None。匹配成功后，选择 next_run 最小的任务时间。

## 引用源码

### schedule/__init__.py:191-192

```text
191:         if not jobs_filtered:
192:             return None
```

### schedule/__init__.py:193-193

```text
193:         return min(jobs_filtered).next_run
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
