# 新仓库确认：Tenacity 来源优先复核准备

2026-10-23。待评答案/条件读取前，已核验 Tenacity 固定快照 `bcb069b93214c1a0a2f7e705`、commit `32ac68f0941319c0d2f4fb927f96e4ee29bb46c6` 的 14 个来源窗口及各完整文件 SHA-256。未运行目标代码，`human_participants=0`，没有本地生成请求。

| 题目 | 预先固定的事实与边界 |
| --- | --- |
| 延迟停止 | `stop_after_delay` 只查失败时已用时间，9 秒仍可进入 3 秒等待；`stop_before_delay` 查已用时间+即将等待，9+3≥10 则停止。`_run_wait` 在 `_run_stop` 前安排；停止后不进入 `DoSleep`。这不是抢占已开始网络调用的硬超时。 |
| after 回调 | 默认异常策略下，成功结果不满足 retry，控制分支直接返回 result，跳过 after。先两次异常后成功，after 最后记录 attempt_number 2；仓库测试确实断言 2。不能把它推广为任意自定义 result-retry 下的成功都不会触发 after。 |
| 错误回调与重抛 | 达到停止条件先查 `retry_error_callback`，存在时添加该 action 并提前返回，不进入依据 reraise 重抛的分支。本题回调返回字符串 fallback，经 iter 的最后 action 返回值，再由同步 `__call__` 返回；不是把 Future 自身当作该字符串。 |
| 追问关闭重试 | 当前 `retry_with(enabled=False)` 构造独立 copy/wrap，本次直接执行原函数一次，原始 ValueError 传播，不被包装为 RetryError。原包装的策略不永久改变。history 是固定合成上下文，不能称实际连续对话。 |
| 私有运行状态 | 源码不能决定公司支付服务昨天的准确次数、等待和堆栈；需要调用配置与运行记录。不得把库默认配置说成生产配置；`insufficient_evidence` 应为 true，额外代码事实仍逐条核验。 |

公开4题与私有题分开计数；重复题只作波动检查，不增加独立题目分母。参考覆盖不能代替最终答案正确性；补读参考不回填模型实际可见来源。后续 Tenacity 与 Blinker 原始匿名判断分别保存，全部到齐后合并并经已有语义验证器导入，揭盲另存。
