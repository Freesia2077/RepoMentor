# RepoMentor 运行记录

SessionRedirectMixin.rebuild_method 对 HEAD、POST、PUT 遇到 301/302/303 时怎样改变请求方法？是否所有重定向都改成 GET？

状态：success；新增模型请求：2；耗时：15.985 秒

SessionRedirectMixin.rebuild_method 方法不会将所有重定向都改成 GET，它对 HEAD、POST 和 PUT 请求的处理逻辑存在特定差异，并非无条件统一转换为 GET。

## 引用源码

### src/requests/sessions.py:333-353

```text
333:     def rebuild_method(self, prepared_request, response):
334:         """When being redirected we may want to change the method of the request
335:         based on certain specs or browser behavior.
336:         """
337:         method = prepared_request.method
338: 
339:         # https://tools.ietf.org/html/rfc7231#section-6.4.4
340:         if response.status_code == codes.see_other and method != "HEAD":
341:             method = "GET"
342: 
343:         # Do what the browsers do, despite standards...
344:         # First, turn 302s into GETs.
345:         if response.status_code == codes.found and method != "HEAD":
346:             method = "GET"
347: 
348:         # Second, if a POST is responded to with a 301, turn it into a GET.
349:         # This bizarre behaviour is explained in Issue 1704.
350:         if response.status_code == codes.moved and method == "POST":
351:             method = "GET"
352: 
353:         prepared_request.method = method
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
