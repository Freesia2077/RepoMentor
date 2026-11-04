"""Explicit Codex judgments of inspected v2 development answers (no model calls)."""

import json
from copy import deepcopy

from p63_dataset import OUT
from record_p63_reviews import assessment as claim
from record_p63_reviews import ref, verdict
from run_rag_quality import frozen


def missing(note):
    return verdict("unverifiable", "not_applicable", "insufficient", note,
                   claim("没有可发布答案", "unverifiable", note), entry="unverifiable")


def reviews():
    rows = json.loads((OUT / "runs/development-v1/review-decisions.json").read_text(encoding="utf-8"))
    u, ex, p = "src/requests/utils.py", "src/requests/exceptions.py", "src/flask/json/provider.py"
    rows["sweqa-flask-001--sdk/answer"] = verdict("partially_correct", "partial", "partial",
        "控制转义的方向对，但 ASCII A 的 \\u0041 示例不符合 ensure_ascii 的非 ASCII 转义行为，且未给 True/False 对应关系。",
        claim("ensure_ascii 控制 Unicode 转义", "partial", "源码注释限定非 ASCII 字符，177 行仅透传参数。", ref(p, 144, 148), ref(p, 177, 177)))
    rows["sweqa-requests-001--fixed/answer"] = verdict("incorrect", "partial", "partial",
        "开头否定显式 no_proxy 参数优先，后面的绕过和 getproxies 描述无法修正这一错误。",
        claim("显式参数不覆盖环境优先级", "contradicted", "仅参数为 None 时读取环境变量。", ref(u, 769, 771)),
        claim("命中绕过返回空字典，否则返回 getproxies", "supported", "包装函数确实如此。", ref(u, 816, 825)))
    rows["sweqa-requests-001--sdk/answer"] = verdict("partially_correct", "partial", "partial",
        "绕过/非空字典含义正确；把绕过机制当作参数优先级，缺少 no_proxy is None 的环境回退条件。",
        claim("显式 no_proxy 优先且非空意味着未绕过", "partial", "参数优先级由被调函数的 None 分支决定，引用仅证明包装函数。", ref(u, 769, 771), ref(u, 816, 825)))
    rows["sweqa-requests-002--sdk/answer"] = verdict("partially_correct", "partial", "insufficient",
        "回答了样例是文本字符串，但未回答单一编码断言、规范化边界及 HTTP headers 题干错误。",
        claim("样例使用 HTML/XML 字符串", "supported", "输入确实是字符串，但这不是题目所需全部测试语义。", ref("tests/test_utils.py", 368, 392)),
        claim("未解释编码值原样返回", "partial", "findall 拼接没有大小写规范化。", ref(u, 493, 501)))
    rows["sweqa-requests-003--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "将相同的继承列表解释为层级差异，且没有解释 RequestException 与 ValueError 的区别。",
        claim("InvalidSchema 和 MissingSchema 的继承层级不同", "contradicted", "两个类都直接继承 RequestException 和 ValueError。", ref(ex, 99, 104)))
    rows["history-dev-01--fixed/answer"] = verdict("incorrect", "unsupported", "partial",
        "将 Node 负数行为误判为转零返回空串，引用压缩浏览器入口与无关测试条件。",
        claim("Node 负数转零且相关测试在 123–124", "contradicted", "实际显式 RangeError，负数测试在 87–93。", ref("index.js", 133, 138), ref("test/index.test.js", 87, 93)), entry="mixed")
    rows["history-dev-01--sdk/answer"] = missing("首个研究请求输出截断，没有生成可发布答案；保留失败。")
    rows["history-dev-02--fixed/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "Node 负数返回空串且不抛错的结论错误，引用了压缩浏览器入口。",
        claim("Node 负数返回空串不抛错", "contradicted", "默认绑定 customAlphabet，负数显式抛 RangeError。", ref("index.js", 133, 138), ref("index.js", 177, 177)), entry="mixed")
    del rows["history-dev-02--sdk/answer"]  # Actual blocked parent: no fabricated answer/review.
    rows["history-dev-03--sdk/answer"] = missing("最终引用越界，结果被拒绝发布；不将 raw response 当成成功答案。")
    return rows


def hybrid_reviews():
    rows = deepcopy(reviews())
    for mode in ("fixed", "sdk"):
        rows[f"sweqa-flask-001--{mode}/answer"] = verdict("correct", "supported", "partial",
            "正确解释默认 True 和非 ASCII 转义，未说明关闭时的原始 Unicode 输出。沿用 v1 的完整性标准。",
            claim("默认 True 转义非 ASCII 字符", "supported", "参数文档直接支持。", ref("src/flask/json/provider.py", 144, 148)))
    rows["sweqa-flask-003--sdk/answer"]["notes"] += " 同时标记资料不足，但未说明具体缺口。"
    rows["history-dev-01--fixed/answer"] = verdict("incorrect", "unsupported", "partial",
        "否认存在负数测试，实际上该快照有明确的 -10 throws 断言。",
        claim("测试没有验证负数", "contradicted", "test/index.test.js 87–93 明确验证负数抛错。", ref("test/index.test.js", 87, 93)), entry="mixed")
    rows["history-dev-01--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "把浏览器压缩代码当成 Node 实现，并误称没有负数校验。",
        claim("Node 实现没有负数特殊处理", "contradicted", "size < 0 分支显式抛错。", ref("index.js", 133, 138)), entry="mixed", abstention="inappropriate")
    rows["history-dev-02--fixed/answer"] = verdict("partially_correct", "unsupported", "partial",
        "RangeError 结论对，但 Node 入口及异常来源都错：不是 crypto.getRandomValues 抛出负长度异常。",
        claim("Node 负数抛 RangeError", "supported", "实际是 customAlphabet 的显式分支。", ref("index.js", 133, 138)),
        claim("异常来自 getRandomValues", "unsupported", "引用为浏览器构建；其负长度还会先在 Uint8Array 构造时失败。", ref("nanoid.js", 1, 1)), entry="mixed")
    rows["history-dev-02--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "沿用错误入口并称空字符串/无效结果且不显式抛错。",
        claim("Node 负数没有显式抛错", "contradicted", "负数分支显式 RangeError。", ref("index.js", 133, 138)), entry="mixed")
    rows["history-dev-03--fixed/answer"] = verdict("partially_correct", "unsupported", "insufficient",
        "知道结果不同，但将 index.js 称为 non-secure、nanoid.js 称为 Node，且未解释各自结果。",
        claim("两入口结果不同", "supported", "分别返回空串和抛 RangeError。", ref("non-secure/index.js", 19, 29), ref("index.js", 133, 138)),
        claim("入口名称对应 index.js 与 nanoid.js", "unsupported", "实际 non-secure 在独立目录，Node 默认为根 index.js。", ref("non-secure/index.js", 19, 29), ref("index.js", 177, 177)), entry="mixed")
    rows["history-dev-03--sdk/answer"] = missing("四次请求后输出截断，没有可发布答案。")
    rows["history-dev-04--fixed/answer"] = verdict("correct", "supported", "partial",
        "不转小写结论正确，但没有按题目结合实现和测试解释。",
        claim("on 不统一转小写", "supported", "测试对原始大小写键与小写键作相反断言。", ref("test/index_test.ts", 79, 90)))
    return rows


def rerank_reviews():
    rows = hybrid_reviews()
    rows["sweqa-flask-001--sdk/answer"] = verdict("correct", "supported", "adequate",
        "覆盖默认 True 的转义以及 False 的原始 Unicode 输出，参数说明和实际传递支持结论。",
        claim("ensure_ascii True 转义、False 保留 Unicode", "supported", "属性说明与 dumps 的 setdefault 支持。", ref("src/flask/json/provider.py", 144, 148), ref("src/flask/json/provider.py", 166, 179)))
    rows["sweqa-flask-003--fixed/answer"] = verdict("unverifiable", "not_applicable", "insufficient",
        "拒答来自检索未找到蓝图缓存机制，仓库本身存在答案。",
        claim("未回答静态资源与缓存机制", "unverifiable", "两个组件有 get_send_file_max_age 与 send_static_file。", ref("src/flask/blueprints.py", 55, 102)), abstention="inappropriate")
    rows["sweqa-flask-003--sdk/answer"] = verdict("incorrect", "unsupported", "partial",
        "七次请求后将应用工厂与 cached_property 误作 HTTP 静态缓存一致性的原因。",
        claim("cached_property 确保静态文件缓存一致", "contradicted", "真正路径由 SEND_FILE_MAX_AGE_DEFAULT/get_send_file_max_age/max_age 控制。", ref("src/flask/blueprints.py", 55, 102), ref("src/flask/app.py", 281, 328)), entry="mixed")
    rows["sweqa-requests-001--sdk/answer"] = verdict("correct", "partial", "adequate",
        "参数优先和非空字典含义均回答，但优先级引用缺少被调用函数的 None 分支。",
        claim("显式绕过参数优先于环境变量", "supported", "源码支持，但答案只引用 wrapper。", ref("src/requests/utils.py", 769, 771)),
        claim("非空表明未绕过且有代理配置", "supported", "getproxies 分支支持，不证明代理连通。", ref("src/requests/utils.py", 816, 825)))
    rows["sweqa-requests-003--fixed/answer"] = verdict("partially_correct", "partial", "partial",
        "双重继承和输入错误/请求上下文的核心解释方向正确，但正文停在未闭合短语，且把具体 adapter 选择笼统称为 URL 解析。",
        claim("两父类分别提供请求上下文和值错误语义", "supported", "继承声明和 RequestException 初始化支持。", ref("src/requests/exceptions.py", 12, 24), ref("src/requests/exceptions.py", 103, 104)),
        claim("正文解释完成", "partial", "实际正文最后止于“无效的 schema”，JSON 合法不等于正文完整。"))
    rows["sweqa-requests-003--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "误称 InvalidURL 只继承 RequestException，且未解释题目要求的两个父类语义。",
        claim("InvalidURL 只继承 RequestException", "contradicted", "InvalidURL 同样继承 ValueError。", ref("src/requests/exceptions.py", 103, 108)))
    for mode in ("fixed", "sdk"):
        rows[f"history-dev-01--{mode}/answer"] = verdict("correct", "supported", "adequate",
            "负数抛错和 throws 测试验证均正确。SDK 附带浏览器构建引用不提供 Node 定位价值，但测试原文足以支持这两项结论。",
            claim("负数抛错且测试使用 throws 验证", "supported", "测试实际调用 nanoid(-10)，验证错误消息。", ref("test/index.test.js", 87, 93)))
    rows["history-dev-02--fixed/answer"] = verdict("correct", "partial", "partial",
        "抛错结论正确，仍只给测试与浏览器压缩构建，未按追问解释 Node 实现依据。",
        claim("负数抛错而非返回空串", "supported", "负数测试支持。", ref("test/index.test.js", 87, 93)))
    rows["history-dev-02--sdk/answer"] = verdict("incorrect", "unsupported", "insufficient",
        "明确要求沿用 Node，却引用 non-secure 的零长度测试并改答空串，跨轮入口串用。",
        claim("Node 返回空串不抛错", "contradicted", "Node 的负数分支显式 RangeError。", ref("index.js", 133, 138)), entry="mixed")
    return rows


if __name__ == "__main__":
    frozen(OUT / "runs/development-v2-bm25/review-decisions.json", reviews())
    frozen(OUT / "runs/development-v2-hybrid/review-decisions.json", hybrid_reviews())
    frozen(OUT / "runs/development-v2-hybrid-rerank/review-decisions.json", rerank_reviews())
