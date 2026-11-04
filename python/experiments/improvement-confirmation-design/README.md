# 两个新仓库的确认设计

2026-10-23。在本轮模型生成前冻结 10 题：8 道源码可答、2 道私有运行信息边界题。全部仅用于确认，禁止进入后训练；源码评审由 Codex 完成，`human_approved=false`。

| 仓库 | 固定 commit | 许可 |
| --- | --- | --- |
| pallets-eco/blinker | `c3364059663df1ddce32799d6b1922af89a345f6` | MIT，完整声明见 [副本](attribution/blinker-LICENSE.txt) |
| jd/tenacity | `32ac68f0941319c0d2f4fb927f96e4ee29bb46c6` | Apache-2.0，完整许可见 [副本](attribution/tenacity-LICENSE) |

最初候选名称 `pallets/blinker` 并不存在；实际公开仓库为 `pallets-eco/blinker`。使用 `SnapshotStore.import_repository` 获取 Git 对象后固定完整 SHA，没有导入目标 Python 包、执行目标源码或测试。许可文件路径和内容 SHA256 在 [repository_cards](dataset.json)；保留许可不等于法律审核。

## 冻结产物

- [questions.json](questions.json)：只列问题输入、不可变仓库身份、固定追问历史来源和预选重复题。SHA256 为 `9fb04ea81383972fe799195dd2261343340c19b1bc71f99739cc124734d5ed9d`。
- [dataset.json](dataset.json)：任务和独立的 `evaluation_only`。必要命题、评分边界、允许源码行段、原文及文件/行段 SHA256 均在后者；允许参考外的有效来源与语义等价答案。
- [plan-fragment.json](plan-fragment.json)：可并入 `benchmark prepare --plan` 的 `source`、`new_tasks`、`new_references`、`excluded_paths`、`repository_cards` 和 `repeat_task_ids`。具体条件、预算和候选实现由运行计划另行冻结。
- [manifest.json](manifest.json)：上述设计及完整许可副本的字节哈希，另绑定生成脚本。manifest SHA256 为 `bde01987a623b56c497994994e65f7fb5322c5d9ed6ce0b11c20adfafde1ea92`。
- [retrieval-check.json](retrieval-check.json)：设计冻结后的离线 CPU BM25 来源检查；网络连接被阻断，模型请求为 0。此项衍生产物另由 [retrieval-check-manifest.json](retrieval-check-manifest.json) 绑定。

`input` 只含 `question`、可选 `history` 和 `query`。两个追问历史由模型预写，内容只声明用户先前条件和中性助手应答，所有条件使用相同历史；不是对各条件真实前轮输出的连续追问。核心执行器必须显式传递历史与 query，不能悄悄降为只读问题正文。

## 题目覆盖

下表只介绍题型；答案及命题保留在 `evaluation_only`，不注入普通模型条件。

| task_id 后缀 | 使用疑问 | 工作流 |
| --- | --- | --- |
| blinker-01 | 局部回调注册后收不到事件；如何注册并验证 | 行为、修正及测试定位 |
| blinker-02 | 同一回调同时订阅通用和指定发送者 | 机制及测试定位；预选重复题 |
| blinker-03 | 临时订阅块抛出 BaseException 后的清理 | 异常路径及测试定位 |
| blinker-04 | 从全同步切到同步与异步接收器混用 | 固定历史条件切换 |
| blinker-05 | 公司生产订单信号次数与最慢回调 | 私有运行数据边界 |
| tenacity-01 | 重试总时限是否包含下一次等待 | 跨文件条件与调用顺序；预选重复题 |
| tenacity-02 | after 是否覆盖首次成功和最后成功 | 控制分支及测试定位 |
| tenacity-03 | reraise 与结束回调同时配置 | 配置优先级及返回路径 |
| tenacity-04 | 单次关闭重试是否更改原装饰器 | 固定历史条件切换 |
| tenacity-05 | 公司支付服务的真实重试和堆栈 | 私有运行数据边界 |

两道预选代表题在各运行条件下额外独立执行一次，沿用相同 seed；原次和重复次均报告，不择优，也不把两个重复题称为统计泛化证明。

## 去重与解释边界

仓库身份未出现在原登记 28 库中；10 题按现有题库 NFC/换行/首尾空白规范化比对，无相同问题，且 canonical identity 不重复。这个检查不自动消除语义改写。题库原字节 SHA256 记录在 `questions.json`，原题库、训练集和最终测试均未改动。

源码检查采用至少 80 tokens 文件的 20-token shingles，跨新旧库 containment≥0.8 时排除新侧文件。新库共扫描 25 文件，对现有非封存 24 库 932 文件，没有达阈值匹配。原 P0 四个封存 test 库未打开，其旧报告没有可复用的逐文件 shingles，因此不能声称完成了与这四库的文件级近重检查。短片段、语义克隆和公开库的模型预训练污染仍不能排除。

离线来源检查中，10 题的输入均满足当前窗口预算；8 道可答题均未覆盖所有参考非空行。参考段覆盖是定位缺口的代理指标，部分参考包含实现、测试或备选证明，不能直接判为答案错误或声称 Agent 一定能改善。该检查没有用于改题、替换参考、改变检索配置或挑选确认题。

此设计覆盖两个小型 Python 库的若干实际 API 使用问题，由实现者选择并核对源码；不是官方基准、独立人审或大型仓库泛化实验。真人使用收益需另有真实参与者记录；模型或子 Agent 试用不能改称真人研究。

冻结设计由统一归档检查验证；原设计脚本已归入[历史方法源码包](../improvement-study/method-source.zip)，不再作为活动入口：

```powershell
.\run.ps1 scripts/verify_archives.py
```
