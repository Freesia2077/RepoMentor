# RepoMentor 运行记录

SessionRedirectMixin.rebuild_method 对 HEAD、POST、PUT 遇到 301/302/303 时怎样改变请求方法？是否所有重定向都改成 GET？

状态：success；新增模型请求：1；耗时：9.203 秒

SessionRedirectMixin.rebuild_method 不会将所有重定向都改为 GET，而是根据响应状态码和原始请求方法有条件地修改方法：对于 302 (Found) 和 303 (See Other) 响应，无论原始方法是什么（HEAD 除外），都会强制改为 GET；对于 301 (Moved）响应，只有当原始方法是 POST 时才会改为 GET，其他情况保持原方法不变。

## 引用源码

### src/requests/sessions.py:340-341

```text
340:         if response.status_code == codes.see_other and method != "HEAD":
341:             method = "GET"
```

### src/requests/sessions.py:345-346

```text
345:         if response.status_code == codes.found and method != "HEAD":
346:             method = "GET"
```

### src/requests/sessions.py:350-351

```text
350:         if response.status_code == codes.moved and method == "POST":
351:             method = "GET"
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
