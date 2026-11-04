# RepoMentor 运行记录

select_proxy 对 https://example.com/a 在同时配置 https://example.com、https、all://example.com、all 时按什么顺序选择？第一个键存在但值为 None 时会继续回退吗？

状态：success；新增模型请求：1；耗时：5.672 秒

select_proxy 对 https://example.com/a 的选择顺序为：1. https://example.com；2. https；3. all://example.com；4. all。若第一个键（https://example.com）存在但值为 None，函数会立即返回 None，不会继续回退到后续键。

## 引用源码

### src/requests/utils.py:839-849

```text
839:     proxy_keys = [
840:         urlparts.scheme + "://" + urlparts.hostname,
841:         urlparts.scheme,
842:         "all://" + urlparts.hostname,
843:         "all",
844:     ]
845:     proxy = None
846:     for proxy_key in proxy_keys:
847:         if proxy_key in proxies:
848:             proxy = proxies[proxy_key]
849:             break
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
