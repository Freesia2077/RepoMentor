# RepoMentor 运行记录

get_jobs() 不传 tag 返回内部列表本身吗？

状态：success；新增模型请求：1；耗时：6.343 秒

是的，当 `get_jobs()` 不传 `tag` 参数（即 `tag=None`）时，它会返回内部列表 `self.jobs` 的副本。

## 引用源码

### schedule/__init__.py:130-131

```text
130:         if tag is None:
131:             return self.jobs[:]
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
