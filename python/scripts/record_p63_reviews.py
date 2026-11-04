"""Codex-authored judgments after reading answers and snapshot sources; never an auto-grader."""

from p63_dataset import OUT
from run_rag_quality import frozen


def ref(path, start, end):
    return {"path": path, "start_line": start, "end_line": end}


def verdict(correctness, support, completeness, notes, *claims, abstention="not_applicable", consistency="consistent", entry="correct"):
    return {"correctness": correctness, "citation_support": support, "completeness": completeness,
            "abstention": abstention, "entry_scope": entry, "consistency": consistency,
            "notes": notes, "claims": [{"claim": text, "status": status, "rationale": reason, "evidence": sources}
                                         for text, status, reason, sources in claims]}


def assessment(text, status, reason, *sources):
    return text, status, reason, list(sources)


def initial_reviews():
    p = "src/flask/json/provider.py"
    bp = "src/flask/sansio/blueprints.py"
    u = "src/requests/utils.py"
    ex = "src/requests/exceptions.py"
    nt = "test/index.test.js"
    mt = "test/index_test.ts"
    rows = {}
    for mode in ("fixed", "sdk"):
        rows[f"sweqa-flask-001--{mode}/answer"] = verdict("correct", "partial", "partial",
            "说明控制转义/原字符，但未说明 True/False 对应关系；引用只是 setdefault 参数传递，缺参数语义注释或测试。",
            assessment("ensure_ascii 控制非 ASCII 转义", "supported", "参数注释支持，但模型引用 176–178 本身不足以支持具体转换含义。", ref(p, 144, 148)))
        rows[f"sweqa-flask-002--{mode}/answer"] = verdict("unverifiable", "not_applicable", "insufficient",
            "拒答是检索未取得相关守卫，不是仓库缺少答案；不算解决。",
            assessment("未回答注册后为何拒绝 setup", "unverifiable", "实际源码包含注册标志与一致性解释。", ref(bp, 213, 221), ref(bp, 319, 335)),
            abstention="inappropriate")
        rows[f"sweqa-flask-003--{mode}/answer"] = verdict("partially_correct", "partial", "partial",
            "定位蓝图概念但未解释共享缓存钩子、配置项及显式 max_age 传递；引用不支持统一缓存细节。",
            assessment("蓝图分离组件与静态资源", "supported", "蓝图文档和静态资源函数支持。", ref("docs/blueprints.rst", 8, 14), ref("src/flask/blueprints.py", 82, 102)),
            assessment("一致缓存机制", "partial", "需说明 get_send_file_max_age/SEND_FILE_MAX_AGE_DEFAULT，而答案只有概括。", ref("src/flask/blueprints.py", 55, 102), ref("src/flask/app.py", 281, 328)))
    rows["sweqa-requests-001--fixed/answer"] = verdict("incorrect", "partial", "partial",
        "否定参数优先级与实现相反；非空字典也不证明 URL 匹配某个环境代理规则，仅说明未绕过且代理配置非空。",
        assessment("no_proxy 不覆盖环境变量优先级", "contradicted", "只有显式参数为 None 时才读取环境 no_proxy。", ref(u, 769, 771)),
        assessment("非空表明匹配 HTTP_PROXY", "unsupported", "getproxies 返回配置，URL 代理选择还在后续步骤。", ref(u, 816, 825)))
    rows["sweqa-requests-001--sdk/answer"] = verdict("partially_correct", "partial", "partial",
        "知道绕过与非空含义，但将调用 should_bypass_proxies 本身当作参数优先级机制，未看到 None 分支。",
        assessment("参数优先且非空表示未绕过", "partial", "核心结论方向正确，优先级需要 769–771 支持，而引用仅 822–825。", ref(u, 769, 771), ref(u, 816, 825)))
    rows["sweqa-requests-002--fixed/answer"] = verdict("partially_correct", "partial", "partial",
        "仅回答单一编码，遗漏大小写原样保留、未做规范化和题干 HTTP headers 纠偏。引用还未到 len 断言。",
        assessment("每个样例恰有一个编码", "supported", "实际断言在 390–392；模型只引用 368–384 样例列表。", ref("tests/test_utils.py", 368, 392)))
    rows["sweqa-requests-002--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "把测试预设的大写示例误当作函数执行大写规范化，源码仅返回正则捕获值。",
        assessment("无论输入都规范成大写", "contradicted", "findall 后直接拼接结果，没有 upper/lower。", ref(u, 493, 501), ref("tests/test_utils.py", 368, 392)))
    rows["sweqa-requests-003--fixed/answer"] = verdict("correct", "supported", "adequate",
        "双重继承、输入错误与请求异常上下文的区别均正确；额外 MissingSchema 说明虽冗余但未改变结论。",
        assessment("RequestException 提供 request/response 上下文，ValueError 表示输入问题", "supported", "父类声明及初始化支持。", ref(ex, 12, 24), ref(ex, 99, 104)))
    rows["sweqa-requests-003--sdk/answer"] = verdict("incorrect", "unsupported", "partial",
        "误称 MissingSchema 没有 RequestException 特定行为，并转而比较两个子类，未回答父类的语义差异。",
        assessment("MissingSchema 不具备 RequestException 行为", "contradicted", "二者继承列表相同且都未重写行为。", ref(ex, 99, 104), ref(ex, 12, 24)))
    rows["history-dev-01--fixed/answer"] = verdict("incorrect", "unsupported", "partial",
        "抛错种类写为 TypeError，实际 RangeError；0 长度的 customAlphabet 测试不证明 nanoid 负数行为。",
        assessment("Node nanoid 负数抛 TypeError", "contradicted", "负数分支显式 RangeError。", ref("index.js", 133, 138)),
        assessment("0 长度测试间接证明负数行为", "unsupported", "应检查 -10 的 throws 断言。", ref(nt, 87, 93)))
    rows["history-dev-01--sdk/answer"] = verdict("partially_correct", "unsupported", "partial",
        "负数抛错结论正确，但声称所引 258–261 有 throws，实际只验证 customAlphabet(0)。",
        assessment("负数抛错且 throws 断言验证", "partial", "真正的 nanoid 负数测试位于 87–93。", ref(nt, 87, 93), ref(nt, 257, 262)))
    rows["history-dev-02--fixed/answer"] = verdict("correct", "unsupported", "partial",
        "负数抛错结论正确，但没有沿实现给依据；所引行只验证零长度。",
        assessment("负数抛错而零长度返回空串", "supported", "实现支持，模型引用测试 259 不能支持负数结论。", ref("index.js", 133, 138)))
    rows["history-dev-02--sdk/answer"] = verdict("correct", "unsupported", "partial",
        "RangeError 结论正确，却引用 random(bytes) 的分支替代 nanoid→customAlphabet 路径。",
        assessment("Node nanoid 负数抛 RangeError", "supported", "真正路径是绑定 177 与 133–138；单独 random 的 21 行不足。", ref("index.js", 133, 138), ref("index.js", 177, 177)))
    rows["history-dev-03--fixed/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "误说两个入口结果相同，且没有引用 non-secure 源码。",
        assessment("non-secure 与 Node 负数相同", "contradicted", "前者循环不执行返回空串，后者 RangeError。", ref("non-secure/index.js", 19, 29), ref("index.js", 133, 138)), entry="mixed")
    rows["history-dev-03--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "两入口原文均已进入最终输入，仍把分支结论写反并虚构正长度 10；属于提供信息后的模型误读。",
        assessment("non-secure 抛错而 Node 生成 10 字符", "contradicted", "代码分别返回空串与抛 RangeError。", ref("non-secure/index.js", 19, 29), ref("index.js", 133, 138)))
    rows["history-dev-04--fixed/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "把 should NOT normalize case 的明确否定误读为转小写。",
        assessment("on 将名字转成小写", "contradicted", "测试断言小写键不存在，on 原样使用 type。", ref(mt, 79, 90), ref("src/index.ts", 66, 73)))
    rows["history-dev-04--sdk/answer"] = verdict("correct", "supported", "partial",
        "不转小写结论和测试引用正确，但题目要求结合实现，未补查 on 实现，也未解释测试中的实际断言。",
        assessment("on 严格区分大小写", "supported", "测试断言 FOO/Bar 保留且 foo/bar 不存在。", ref(mt, 79, 90)))
    for mode in ("fixed", "sdk"):
        rows[f"history-dev-05--{mode}/answer"] = verdict("partially_correct", "partial", "adequate",
            "后文 self.jobs[:] 副本解释正确，但对“返回内部列表本身吗”开头答“是的”，前后冲突。",
            assessment("开头肯定列表本身，后文又否定", "partial", "切片确实创建浅拷贝。", ref("schedule/__init__.py", 122, 133)), consistency="contradictory")
        rows[f"history-dev-06--{mode}/answer"] = verdict("correct", "not_applicable", "partial",
            "适当拒答且不编造生产统计，但未解释需要生产日志或遥测的具体缺口。",
            assessment("不能给出未提供的生产统计", "unverifiable", "仓库实现不能确认用户公司的昨日运行统计。"), abstention="appropriate", entry="not_applicable")
    frozen(OUT / "runs/development-v1/review-decisions.json", rows)
    oracle = {
        "sweqa-flask-001--oracle/answer": verdict("correct", "supported", "adequate", "True/False 与 Unicode 输出关系完整且引用支持。",
            assessment("True 转义、False 保留 Unicode", "supported", "属性注释与 JSON 测试共同支持。", ref(p, 144, 148), ref("tests/test_json.py", 48, 54))),
        "sweqa-flask-002--oracle/answer": verdict("correct", "supported", "adequate", "说明注册前完成 setup、注册后 AssertionError 与一致性目的。",
            assessment("注册标志阻止后续 setup", "supported", "检查和设置标志的源码支持。", ref(bp, 213, 221), ref(bp, 319, 320))),
        "sweqa-flask-003--oracle/answer": verdict("partially_correct", "unsupported", "partial", "识别 Blueprint 与 max-age 钩子，却虚构 Blueprint 继承 Flask，且省略共享配置和显式传参。",
            assessment("Blueprint 继承 Flask", "contradicted", "Blueprint 继承 SansioBlueprint，不是 Flask。", ref("src/flask/blueprints.py", 18, 26)),
            assessment("共享 get_send_file_max_age", "supported", "两个组件分别实现同名钩子。", ref("src/flask/blueprints.py", 55, 102), ref("src/flask/app.py", 281, 328))),
        "sweqa-requests-001--oracle/answer": verdict("correct", "partial", "partial", "覆盖参数优先和空字典，却没有回答题目要求的非空字典含义，参数优先引用缺关键分支。",
            assessment("显式 no_proxy 覆盖环境绕过设置", "supported", "None 分支决定读取环境，引用 wrapper 不足。", ref(u, 769, 771)),
            assessment("绕过返回空字典", "supported", "wrapper 的 bypass 分支支持。", ref(u, 816, 825))),
        "sweqa-requests-002--oracle/answer": verdict("partially_correct", "partial", "insufficient", "只说大小写无关，既未解释测试恰好一个 UTF-8，也未纠正编码值规范化和 HTTP headers 前提。",
            assessment("声明匹配不区分大小写", "partial", "HTML 正则使用 re.I，XML 正则没有；也不对编码值规范化。", ref(u, 493, 501))),
        "sweqa-requests-003--oracle/answer": verdict("correct", "supported", "adequate", "父类输入错误和统一请求异常捕获的区别正确，继承声明支持。",
            assessment("双重继承允许两类捕获", "supported", "InvalidSchema 声明两个父类。", ref(ex, 103, 104))),
    }
    frozen(OUT / "runs/oracle-v1/review-decisions.json", oracle)


if __name__ == "__main__":
    initial_reviews()
