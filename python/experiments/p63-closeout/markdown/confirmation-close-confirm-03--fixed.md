# RepoMentor 运行记录

Node 入口导出的 random(-1) 与 random(0) 分别怎样处理？请区分报错与返回的数据类型。

状态：success；新增模型请求：1；耗时：8.031 秒

Node 入口导出的 random(-1) 会抛出 RangeError 异常，而 random(0) 会返回一个长度为 0 的 Buffer 对象。

## 引用源码

### index.js:21-21

```text
21:   if (bytes < 0) throw new RangeError('Wrong ID size')
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
