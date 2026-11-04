# RepoMentor 运行记录

那么 off('FOO') 不传 handler，是删除 Map 的键还是清空对应数组？

状态：success；新增模型请求：2；耗时：27.937 秒

off('FOO') 不传 handler 时，会清空 Map 中对应键的数组（将其设为空数组 []），而不是删除该键。

## 引用源码

### src/index.ts:88-88

```text
88: 					all!.set(type, []);
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
