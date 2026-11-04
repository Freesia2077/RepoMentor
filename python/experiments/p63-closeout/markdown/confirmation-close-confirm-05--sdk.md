# RepoMentor 运行记录

Scheduler.run_all 与 run_pending 在挑选和排序任务上有何区别？run_all 遍历原始列表还是副本？

状态：success；新增模型请求：2；耗时：12.312 秒

run_all 遍历原始列表并执行所有任务，而 run_pending 仅遍历符合条件且已排序的任务列表；run_all 使用切片操作获取副本，run_pending 使用生成器表达式过滤后排序。

## 引用源码

### schedule/__init__.py:118-118

```text
118:         for job in self.jobs[:]:
```

### schedule/__init__.py:99-100

```text
99:         runnable_jobs = (job for job in self.jobs if job.should_run)
100:         for job in sorted(runnable_jobs):
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
