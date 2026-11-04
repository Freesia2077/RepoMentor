# RepoMentor 运行记录

Scheduler.every(2) 本身会把 Job 加入 jobs 吗？正常设置时间单位后，Job.do 怎样保存回调及参数、加入列表，又返回什么？

状态：success；新增模型请求：1；耗时：6.468 秒

Scheduler.every(2) 本身不会把 Job 加入 jobs 列表；它仅创建一个未配置的 Job 实例并返回。正常设置时间单位后，Job.do 通过调用 self.scheduler.add_job(self) 将自身加入 scheduler.jobs 列表，并返回 self。

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

### schedule/__init__.py:209-258

```text
209: class Job:
210:     """
211:     A periodic job as used by :class:`Scheduler`.
212: 
213:     :param interval: A quantity of a certain time unit
214:     :param scheduler: The :class:`Scheduler <Scheduler>` instance that
215:                       this job will register itself with once it has
216:                       been fully configured in :meth:`Job.do()`.
217: 
218:     Every job runs at a given fixed time interval that is defined by:
219: 
220:     * a :meth:`time unit <Job.second>`
221:     * a quantity of `time units` defined by `interval`
222: 
223:     A job is usually created and returned by :meth:`Scheduler.every`
224:     method, which also defines its `interval`.
225:     """
226: 
227:     def __init__(self, interval: int, scheduler: Optional[Scheduler] = None):
228:         self.interval: int = interval  # pause interval * unit between runs
229:         self.latest: Optional[int] = None  # upper limit to the interval
230:         self.job_func: Optional[functools.partial] = None  # the job job_func to run
231: 
232:         # time units, e.g. 'minutes', 'hours', ...
233:         self.unit: Optional[str] = None
234: 
235:         # optional time at which this job runs
236:         self.at_time: Optional[datetime.time] = None
237: 
238:         # optional time zone of the self.at_time field. Only relevant when at_time is not None
239:         self.at_time_zone = None
240: 
241:         # datetime of the last run
242:         self.last_run: Optional[datetime.datetime] = None
243: 
244:         # datetime of the next run
245:         self.next_run: Optional[datetime.datetime] = None
246: 
247:         # Weekday to run the job at. Only relevant when unit is 'weeks'.
248:         # For example, when asking 'every week on tuesday' the start_day is 'tuesday'.
249:         self.start_day: Optional[str] = None
250: 
251:         # optional time of final run
252:         self.cancel_after: Optional[datetime.datetime] = None
253: 
254:         self.tags: Set[Hashable] = set()  # unique set of tags for the job
255:         self.scheduler: Optional[Scheduler] = scheduler  # scheduler to register with
256: 
257:     def __lt__(self, other) -> bool:
258:         """
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
