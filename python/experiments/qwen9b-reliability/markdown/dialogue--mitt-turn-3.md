# RepoMentor 运行记录

切换到 emit，普通 handler 和通配符 handler 的参数、调用顺序分别是什么？

状态：success；新增模型请求：2；耗时：17.86 秒

普通 handler 只接收事件数据 `evt`，通配符 handler 接收事件类型 `type` 和事件数据 `evt`；调用顺序是先执行所有类型匹配的普通 handler，再执行通配符 handler。

## 引用源码

### src/index.ts:108-109

```text
108: 					.map((handler) => {
109: 						handler(evt!);
```

### src/index.ts:117-118

```text
117: 					.map((handler) => {
118: 						handler(type, evt!);
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
