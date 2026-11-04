"""Codex's explicit, source-read confirmation judgments; not an automatic grader."""

from p63 import load
from p63_dataset import OUT
from record_p63_reviews import assessment, verdict
from record_p63_v2_reviews import missing
from run_rag_quality import frozen


def decisions():
    gold = load(OUT / "evaluation-only/confirmation.json")
    rows = {}

    def put(task, mode, correctness, support, completeness, note, *claims, **kwargs):
        evidence = gold[task]["sources"]
        rows[task + "--" + mode + "/answer"] = verdict(correctness, support, completeness, note,
            *(assessment(text, status, reason, *evidence) for text, status, reason in claims), **kwargs)

    put("mitt-confirm-01", "fixed", "correct", "supported", "adequate", "问题只问调用次数；一次的直接结论和三个方法引用足够。",
        ("调用一次", "supported", "push 两次，splice 一项，emit 剩余一项。"))
    put("mitt-confirm-01", "sdk", "incorrect", "unsupported", "adequate", "将一次 off 当成删除全部重复注册。",
        ("调用零次", "contradicted", "off 指定 handler 只 splice 一项，仍剩一项。"))
    put("mitt-confirm-02", "fixed", "partially_correct", "partial", "partial", "清空值而保留键正确；missingHandler 部分自相矛盾，遗漏未找到时不修改数组。开头“是的”也与不删除键冲突。",
        ("省略 handler 保留键、设置空数组", "supported", "set(type, []) 支持。"),
        ("不存在的 handler 被移除后数组既包含它又为空", "contradicted", "indexOf=-1 转为大整数，splice 不删除已有项。"), consistency="contradictory")
    put("mitt-confirm-02", "sdk", "incorrect", "unsupported", "partial", "错误声称省略 handler 删除 Map 键值对。",
        ("off 无 handler 删除键值对", "contradicted", "实现只 set 空数组，没有 delete。"))
    put("mitt-confirm-03", "fixed", "correct", "supported", "adequate", "普通/通配符的先后和实参完整，实际源码与 README 支持。",
        ("先 handler(evt)，后 handler(type, evt)", "supported", "emit 的两段调用顺序和形参支持。"))
    put("mitt-confirm-03", "sdk", "partially_correct", "partial", "partial", "先后顺序正确，但只说参数顺序不同，未说明一个参数与两个参数及其内容。",
        ("普通处理器先于通配符", "supported", "两段顺序支持。"),
        ("参数差异仅描述为顺序不同", "partial", "实际是 evt 与 type,evt，缺必要参数说明。"))
    put("mitt-confirm-04", "fixed", "correct", "partial", "adequate", "共享对象与修改结论都正确，但引用 46–52 只到入参初始化，没有返回 all 与 on 的修改逻辑。",
        ("同一 Map，注册会修改传入容器", "supported", "完整实现 52、58、66–73 支持，答案引用范围不足。"))
    rows["mitt-confirm-04--sdk/answer"] = missing("首个研究请求输出截断，没有可发布答案。")
    put("nanoid-confirm-01", "fixed", "partially_correct", "unsupported", "partial", "不分配新池的结论对，理由却虚构复用 crypto 已有池，且引用浏览器压缩文件，未沿 Node 默认导出。",
        ("nanoid(0) 不分配新池", "supported", "size===0 在 pool 分配前直接返回空串。"),
        ("复用 crypto 已有随机池", "contradicted", "零长度在访问随机池之前返回。"), entry="mixed")
    put("nanoid-confirm-01", "sdk", "partially_correct", "unsupported", "partial", "不分配新池的结论对，但将浏览器 getRandomValues 当作 Node 零长度路径。",
        ("零长度直接 getRandomValues 生成新字节", "contradicted", "Node 默认闭包的零长度分支直接返回。"), entry="mixed")
    for mode in ("fixed", "sdk"):
        put("nanoid-confirm-02", mode, "correct", "supported", "adequate", "否定快速路径并指出 customRandom 回退，引用就是大于 255 的判断和返回。",
            ("字符码超过 255 时回退 customRandom", "supported", "charCodeAt 条件分支直接返回 customRandom。"))
    put("nanoid-confirm-03", "fixed", "correct", "supported", "partial", "正确区分乘 alphabet.length 与乘 64，但未解释按位取整后作为字符索引。",
        ("两种缩放分别使用 alphabet.length 和 64", "supported", "两个循环的索引表达式支持；取整步骤遗漏。"))
    put("nanoid-confirm-03", "sdk", "correct", "supported", "adequate", "两个索引计算公式完整，包括 |0 取整，均引用实际选择字符的语句。",
        ("分别使用 (Math.random()*alphabet.length)|0 与 (Math.random()*64)|0", "supported", "non-secure 两个循环表达式直接支持。"))
    for mode in ("fixed", "sdk"):
        put("nanoid-confirm-04", mode, "correct", "not_applicable", "partial", "拒绝保证线上无碰撞是正确的，但只有“不能确定”，没有说明需要线上 ID 或检测日志的缺口。沿用开发集拒答完整性口径。",
            ("源码不能确认用户今日全部 ID 无碰撞", "unverifiable", "运行数据未提供，源码不等于线上事实。"), abstention="appropriate", entry="not_applicable")
    put("schedule-confirm-01", "fixed", "correct", "supported", "adequate", "过滤条件、切片赋值和列表对象身份均解释正确。",
        ("保留不带标签的任务且不替换列表对象", "supported", "self.jobs[:] 赋值只替换内容。"))
    put("schedule-confirm-01", "sdk", "partially_correct", "unsupported", "adequate", "筛选未标记任务正确，但把切片赋值解释成替换列表对象。",
        ("直接替换 self.jobs 对象", "contradicted", "切片赋值保留原列表身份。"))
    put("schedule-confirm-02", "fixed", "unverifiable", "not_applicable", "insufficient", "相关实现可找到，但固定检索只提供 Scheduler 前部，拒答源于检索失败。",
        ("无法判断是否抛 ValueError", "unverifiable", "cancel_job 的 try/except 明确捕获 ValueError。"), abstention="inappropriate")
    put("schedule-confirm-02", "sdk", "correct", "supported", "adequate", "SDK 补查到 Scheduler.cancel_job，正确回答不向调用者抛 ValueError，引用完整 try/except 实现。",
        ("不向调用者抛 ValueError", "supported", "remove 抛出的 ValueError 被捕获，仅日志记录。"))
    put("schedule-confirm-03", "fixed", "correct", "supported", "adequate", "问题询问是否相同；直接肯定，引用恰是类/实例并列判断。",
        ("CancelJob 类与实例同样处理", "supported", "isinstance(ret, CancelJob) or ret is CancelJob 共用取消路径。"))
    put("schedule-confirm-03", "sdk", "incorrect", "partial", "partial", "先说不同又说 _run_job 相同，并额外虚构 Scheduler.run 的区别；所引后段实际属于 Job.run。",
        ("_run_job 中类与实例处理相同", "supported", "or 条件支持。"),
        ("对问题先回答不同", "contradicted", "两种返回值走同一 cancel_job 分支。"), consistency="contradictory")
    put("schedule-confirm-04", "fixed", "partially_correct", "partial", "adequate", "无任务 None 正确；先称过期时非负，后承认公式为负，再虚构业务中不发生或视为无效。",
        ("无任务 None", "supported", "if not self.next_run 返回 None。"),
        ("过期时不会返回负数", "contradicted", "直接时间差，没有截零。"), consistency="contradictory")
    put("schedule-confirm-04", "sdk", "correct", "supported", "adequate", "完整回答无任务、过期和不截零三点，引用两条实际返回路径。",
        ("无任务 None，过期返回负数且不截零", "supported", "None 分支与时间差表达式支持。"))
    for mode in ("fixed", "sdk"):
        put("more-itertools-confirm-01", mode, "correct", "partial", "adequate", "延迟错误与首组可取的结论正确；所引范围止于 244，缺少 yield 246 和 return ret() 248，未完整支持执行时机。",
            ("不立即报错且第一组可取", "supported", "完整源码在内层生成器迭代末组时才报错；答案引用缺关键 yield/返回。"))
    put("more-itertools-confirm-02", "fixed", "correct", "supported", "partial", "返回值与 _marker 区分方向正确，但正文在未闭合的“用户显式传入的 None”处结束，解释未完成。",
        ("显式 None 返回 None，省略默认值抛 ValueError", "supported", "default is _marker 控制抛错。"))
    put("more-itertools-confirm-02", "sdk", "correct", "supported", "partial", "结果正确，但未回答题目明确要求的为何 None 与未提供不同：_marker 身份哨兵。",
        ("显式 None 与省略参数结果不同", "supported", "源码的身份判断支持。"))
    put("more-itertools-confirm-03", "fixed", "correct", "supported", "adequate", "说明每次取前 n 项为列表、余项不足时全部返回而不填充，并引用调用处与 take 实现。",
        ("take 取前 n 项，不足时返回余项，不补 None", "supported", "partial(take,n,iter(...)) 与 list(islice(...)) 支持。"))
    put("more-itertools-confirm-03", "sdk", "correct", "supported", "partial", "不填充结论正确，但“通过调用 take 消费输入”只是重复题目，未说明按 n 项消费、余项列表或共享迭代器机制。",
        ("不使用 None 填充", "supported", "take 返回 islice 的实际列表。"))
    for mode in ("fixed", "sdk"):
        put("more-itertools-confirm-04", mode, "correct", "not_applicable", "partial", "拒答适当，但没有解释缺少团队生产日志和调用统计。",
            ("不能回答团队昨日生产处理条数", "unverifiable", "没有生产观测数据。"), abstention="appropriate", entry="not_applicable")
    put("flask-confirm-01", "fixed", "incorrect", "unsupported", "adequate", "把 setdefault 误读成覆盖已传值。",
        ("类属性 True 覆盖显式 False", "contradicted", "setdefault 仅在键不存在时设置。"))
    put("flask-confirm-01", "sdk", "correct", "supported", "adequate", "直接正确回答显式 False 保留，所引 setdefault 足以支持。",
        ("显式 sort_keys=False 生效", "supported", "已有键不会被 setdefault 替换。"))
    put("flask-confirm-02", "fixed", "correct", "partial", "partial", "抛错与辅助函数方向正确，但未指出 TypeError，引用仅为禁止同时传参的文档，不支持辅助函数名称/调用关系。",
        ("同时传两类参数抛错，由 _prepare_response_obj 决定", "supported", "完整实现支持，但模型只引 97 行说明。"))
    put("flask-confirm-02", "sdk", "correct", "supported", "adequate", "补查辅助函数后给出 TypeError、禁止同时传参的原因和具体辅助函数，调用与分支引用齐全。",
        ("response 调用 _prepare_response_obj，在同时传参时抛 TypeError", "supported", "78–79 和 104–105 直接支持。"))
    for mode in ("fixed", "sdk"):
        put("flask-confirm-03", mode, "correct", "supported", "adequate", "无 static_folder 时抛错不继续，有配置时调用蓝图自己的 max-age 钩子；两部分都有实现引用。",
            ("无静态目录抛错；max_age 来自 self.get_send_file_max_age", "supported", "94–95 的守卫和 99–102 的取值/传参支持。"))
    put("flask-confirm-04", "fixed", "partially_correct", "partial", "partial", "写文件与返回字符串方向对，但错误否定继承的 dump 自动调用子类 dumps。基类 dumps 自身是抽象占位，还未说明 dump 返回 None。",
        ("dump 写文件，dumps 提供字符串序列化", "supported", "文档与 fp.write 支持。"),
        ("子类必须重写 dump 才能重用 dumps", "contradicted", "self.dumps 动态派发到子类，dump 本身无需重写。"))
    rows["flask-confirm-04--sdk/answer"] = missing("没有可发布的完整答案，保留原始失败状态与原文。")
    for mode in ("fixed", "sdk"):
        put("requests-confirm-01", mode, "correct", "partial", "adequate", "请求优先和 None 键删除都正确；第二个引用止于 for key，第 86 行实际 del 未被引用，删除主张的来源范围不完整。",
            ("请求设置优先，None 键删除", "supported", "update 和 84–86 的删除循环支持，模型引用缺 del 行。"))
    put("requests-confirm-02", "fixed", "partially_correct", "partial", "adequate", "排序与遍历说明正确，但把 lower 后匹配说成区分大小写，与自身依据冲突。",
        ("长前缀优先", "supported", "mount 移动短键，get_adapter 顺序遍历。"),
        ("匹配区分大小写", "contradicted", "URL 和 prefix 都先 lower。"), consistency="contradictory")
    put("requests-confirm-02", "sdk", "partially_correct", "partial", "partial", "最长前缀结论对，未展开配合机制，且错误声称区分大小写。",
        ("最长前缀先匹配", "supported", "两个方法实现支持。"),
        ("匹配区分大小写", "contradicted", "双方 lower 后匹配。"))
    for mode in ("fixed", "sdk"):
        put("requests-confirm-03", mode, "correct", "supported", "adequate", "三种输入的默认编码/None 分支都完整回答且逐条引用。",
            ("text/plain 默认 ISO-8859-1，JSON 默认 utf-8，缺 content-type 返回 None", "supported", "三个分支分别支持。"))
        put("requests-confirm-04", mode, "incorrect", "unsupported", "adequate", "定位到抛 InvalidSchema，却未查该类的双重继承，错误否定 RequestException 捕获。",
            ("InvalidSchema 不继承 RequestException", "contradicted", "exceptions.py 103 明确同时继承 RequestException 和 ValueError。"))
    return rows


if __name__ == "__main__":
    rows = decisions()
    assert len(rows) == 48, "review every frozen confirmation case before publishing"
    frozen(OUT / "runs/confirmation-v1/review-decisions.json", rows)
