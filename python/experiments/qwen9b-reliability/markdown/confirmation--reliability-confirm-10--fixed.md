# RepoMentor 运行记录

Blueprint.record_once(func) 在允许设置的阶段调用时会立即执行 func 吗？record 与 wrapper 各负责什么，first_registration 为 False 或 True 时分别怎样处理？

状态：success；新增模型请求：1；耗时：6.828 秒

Blueprint.record_once(func) 在允许设置的阶段调用时不会立即执行 func，而是将其注册为延迟函数。record 负责将函数直接添加到延迟函数列表中，而 wrapper 负责检查是否为首次注册以决定是否执行 func。当 first_registration 为 True 时执行 func，为 False 时不执行。

## 引用源码

### src/flask/sansio/blueprints.py:224-230

```text
224:     def record(self, func: DeferredSetupFunction) -> None:
225:         """Registers a function that is called when the blueprint is
226:         registered on the application.  This function is called with the
227:         state as argument as returned by the :meth:`make_setup_state`
228:         method.
229:         """
230:         self.deferred_functions.append(func)
```

### src/flask/sansio/blueprints.py:232-244

```text
232:     @setupmethod
233:     def record_once(self, func: DeferredSetupFunction) -> None:
234:         """Works like :meth:`record` but wraps the function in another
235:         function that will ensure the function is only called once.  If the
236:         blueprint is registered a second time on the application, the
237:         function passed is not called.
238:         """
239: 
240:         def wrapper(state: BlueprintSetupState) -> None:
241:             if state.first_registration:
242:                 func(state)
243: 
244:         self.record(update_wrapper(wrapper, func))
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
