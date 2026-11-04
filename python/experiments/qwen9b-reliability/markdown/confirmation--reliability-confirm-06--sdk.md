# RepoMentor 运行记录

Scheduler.every(2) 本身会把 Job 加入 jobs 吗？正常设置时间单位后，Job.do 怎样保存回调及参数、加入列表，又返回什么？

状态：success；新增模型请求：3；耗时：25.75 秒

Scheduler.every(2) 本身不会把 Job 加入 scheduler.jobs 列表；只有调用 Job.do() 且关联了 Scheduler 后，Job 才会被加入列表。

## 引用源码

### schedule/__init__.py:162-170

```text
162:     def every(self, interval: int = 1) -> "Job":
163:         """
164:         Schedule a new periodic job.
165: 
166:         :param interval: A quantity of a certain time unit
167:         :return: An unconfigured :class:`Job <Job>`
168:         """
169:         job = Job(interval, self)
170:         return job
```

### schedule/__init__.py:644-664

```text
644:     def do(self, job_func: Callable, *args, **kwargs):
645:         """
646:         Specifies the job_func that should be called every time the
647:         job runs.
648: 
649:         Any additional arguments are passed on to job_func when
650:         the job runs.
651: 
652:         :param job_func: The function to be scheduled
653:         :return: The invoked job instance
654:         """
655:         self.job_func = functools.partial(job_func, *args, **kwargs)
656:         functools.update_wrapper(self.job_func, job_func)
657:         self._schedule_next_run()
658:         if self.scheduler is None:
659:             raise ScheduleError(
660:                 "Unable to a add job to schedule. "
661:                 "Job is not associated with an scheduler"
662:             )
663:         self.scheduler.jobs.append(self)
664:         return self
```

## 工具轨迹

| 工具 | 状态 | 来源位置 |
| --- | --- | --- |
| read_code | ok | schedule/__init__.py:644-664 |

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
