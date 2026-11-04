"""Codex-authored judgments after reading fixed source, not an automatic grader.

These annotations apply ONLY to the immutable candidates.json. Recompute source
excerpts and validate labels offline; never use these rules to grade new answers.
"""

import json

from prepare_semantic_review import OUT, load
from run_rag_quality import frozen

from repomentor.paths import data_root
from repomentor.repository import SnapshotStore
from repomentor.semantic_review import summarize_reviews, validate_judgment


def evidence(path, start, end):
    return {"path": path, "start_line": start, "end_line": end}


def claim(text, status, rationale, *refs):
    return {"claim": text, "status": status, "rationale": rationale, "evidence": list(refs)}


def main():
    candidates = {c["candidate_id"]: c for c in load(OUT / "candidates.json")}
    identities = load(OUT / "identity-map.json")
    judgments = {}

    def add(source, stage, correctness, support, completeness, claims, notes,
            *, abstention="not_applicable", consistency="consistent", scope="correct", points=None):
        matches = [cid for cid, item in identities.items() if any(
            a["source_file"] == "experiments/" + source and a["stage"] == stage for a in item["appearances"])]
        assert len(matches) == 1, (source, stage, matches)
        cid = matches[0]
        assert cid not in judgments
        value = {"candidate_id": cid, "correctness": correctness, "citation_support": support,
                 "completeness": completeness, "abstention": abstention, "entry_scope": scope,
                 "consistency": consistency, "claims": claims, "notes": notes, "key_points": points}
        reader = SnapshotStore(data_root() / "corpus").open(identities[cid]["snapshot_id"])
        assert reader.snapshot.commit_sha == identities[cid]["commit_sha"]
        judgments[cid] = validate_judgment(value, candidates[cid], reader)

    negative = [claim("Node 默认 nanoid(-10) 抛出 RangeError", "supported",
                      "默认导出绑定 customAlphabet；返回函数在 136–137 行转换并检查，测试明确断言 -10。",
                      evidence("index.js", 133, 138), evidence("index.js", 177, 177),
                      evidence("test/index.test.js", 87, 93))]
    for name in ("chat-nanoid-1", "off--ai--nanoid-p4-cross_negative"):
        add(f"p6-rag-context/cases/{name}.json", "qa", "correct", "supported", "adequate", negative,
            "限定题中 Node 默认入口与 -10 整数；不外推为任意负小数/超范围整数的转换规律。")
    add("p6-rag-context/cases/chat-nanoid-2.json", "qa", "partially_correct", "partial", "adequate", [
        *negative, claim("负数判断位于引用的 133–135 行", "contradicted",
            "133–135 只有函数开头和注释；真正判断在 136–137，模型最终上下文没有保留这两行。",
            evidence("index.js", 133, 138))], "结论得到测试支持；实现行号错误，不能只按结果对错判为完全正确。")
    add("p6-rag-context-fix/cases/chat-nanoid-2.json", "qa", "correct", "supported", "adequate", negative,
        "修正组完整保留关键实现与测试引用，复用上一轮缓存输入，没有将失败覆盖。")
    add("p6-rag-context/cases/chat-nanoid-3.json", "qa", "partially_correct", "partial", "adequate", [
        claim("non-secure nanoid(-10) 返回空串", "supported", "i=-10，条件 i-- > 0 为假，返回初始空串。",
              evidence("non-secure/index.js", 19, 29)),
        claim("size | 0 将负数变成 0", "contradicted", "这里是 32 位整数转换，-10 不会变成 0；停止来自循环条件。",
              evidence("non-secure/index.js", 21, 28))], "入口切换正确，机制解释错误；引用也未包括初始化和 return。")
    add("p6-rag-context/cases/off--developit--mitt-p4-cross_case.json", "qa", "partially_correct", "partial", "adequate", [
        claim("on 不将事件名转成小写", "supported", "Map 直接以 type 为键，测试断言 FOO 与 foo 不同。",
              evidence("src/index.ts", 66, 73), evidence("test/index_test.ts", 79, 90)),
        claim("emit 精确匹配但不区分大小写", "contradicted", "Map 查找保留不同字符串键，答案两种表述自相矛盾。",
              evidence("src/index.ts", 103, 113), evidence("test/index_test.ts", 79, 90))],
        "模型看到完整相关源码仍答错，属于生成一致性失败。", consistency="contradictory")

    purpose = claim("轻量无依赖事件发布订阅，支持通配符与浏览器", "supported",
                    "README 明确描述；functional 在这里指方法不依赖 this，不代表没有状态修改。",
                    evidence("README.md", 11, 19))
    setup = claim("TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本", "supported",
                  "package 明确列出入口及脚本；只确认声明，未实际安装或执行。",
                  evidence("package.json", 5, 27))
    mutable = claim("纯函数式/无副作用", "contradicted", "on 修改 handler 数组和 Map，off 执行 splice/set；functional 文档仅说不依赖 this。",
                    evidence("src/index.ts", 66, 90), evidence("README.md", 14, 16))
    core = claim("Map 存储、on/off 注册注销、emit 触发", "supported",
                 "实现吻合；emit 正文在 103–121，引用 46–94 仅覆盖部分，不意味着所有文字得到其引用支持。",
                 evidence("src/index.ts", 46, 73), evidence("src/index.ts", 82, 121))
    no_imports = evidence("src/index.ts", 1, 123)
    add("p6-rag-context/cases/report-mitt-isolated.json", "explorer", "correct", "supported", "partial",
        [purpose, setup, claim("源码入口默认导出 mitt", "supported", "入口声明明确；尚未分析整个实现。",
                              evidence("src/index.ts", 46, 50))],
        "合理承认未读实现；提示的额外 src 模块并不存在但以缺口提出。fileCount 为原阶段值，最终报告由 Harness 重算；不拿阶段计数当全仓计数。")
    add("p6-rag-context/cases/report-mitt-isolated.json", "mentor", "partially_correct", "partial", "partial", [
        core, setup, mutable,
        claim("src/index.ts 静态依赖 test/index_test.ts", "contradicted", "实现文件没有导入；测试反向导入项目入口。",
              no_imports, evidence("test/index_test.ts", 1, 5))],
        "原 guardrail 已拒绝，保留失败。未读 package 却引用；测试 51–100 主要是 on，不能证明 off；emit 正文未读。")
    add("p6-rag-context/cases/report-mitt-shared.json", "mentor", "partially_correct", "partial", "partial", [
        core, setup, claim("src/index.ts 静态导入 package.json", "contradicted", "固定实现没有该导入。", no_imports),
        claim("通配符在类型匹配处理器后执行", "supported", "注释 95 行和实现第二段吻合。",
              evidence("src/index.ts", 94, 95), evidence("src/index.ts", 103, 121))],
        "来源共享补充了配置，未解决依赖边错误；应区分读到 emit 注释与读到其实现。")
    install = claim("npm 安装及模块/UMD 引入", "supported", "README 给出对应示例。", evidence("README.md", 32, 52))
    add("p6-rag-context/cases/report-mitt-shared.json", "contributor", "partially_correct", "partial", "partial", [
        install, setup, claim("需手动配置 TypeScript 编译器", "unsupported",
                             "作为必须完成的开发前置条件缺乏检查；现有脚本已提供检查流程。", evidence("package.json", 18, 27))],
        "保留 report_grounding；examinedFiles 写了未观察的 src 且缺口称没读。goodFirstIssues 是建议，不是验证过的上游 issue。",
        consistency="contradictory")
    add("p6-rag-context-fix/cases/report-mitt-isolated.json", "explorer", "partially_correct", "partial", "partial", [
        purpose, setup, core, claim("浏览器原生事件发射器", "unsupported",
                                  "README 说为浏览器设计的库，并非浏览器内建 API。", evidence("README.md", 11, 19))],
        "ESM 入口是 package 5/14 行，引用 6/9 不覆盖它；emit 正文未读。重复出现的 Explorer 同一尝试只评一次。")
    add("p6-rag-context-fix/cases/report-mitt-isolated.json", "mentor", "partially_correct", "partial", "partial", [
        core, mutable, claim("实现静态导入自身", "contradicted", "完整实现没有导入语句，自环无依据。", no_imports),
        claim("测试 82–90 验证 off", "contradicted", "该区间是 on 的大小写测试。", evidence("test/index_test.ts", 79, 90))],
        "schema/路径通过并不保证依赖边或阅读指导正确。")
    add("p6-rag-context-fix/cases/report-mitt-isolated.json", "contributor", "partially_correct", "partial", "partial", [
        install, setup, mutable, claim("test/index_test.ts 是类型编译测试入口", "contradicted",
            "专门的 test-types 脚本指向 test/test-types-compilation.ts；不能将两者混为一体。",
            evidence("package.json", 19, 21), evidence("test/test-types-compilation.ts", 9, 24))],
        "npm build/test 有配置依据；建议尚未核实，上游 issue 未验证。")
    add("p6-rag-context-fix/cases/report-mitt-shared.json", "mentor", "partially_correct", "partial", "partial", [
        core, mutable, claim("条件类型区分普通与通配符事件", "contradicted",
            "条件类型在 emit 重载用于事件参数是否允许省略；通配符使用显式重载。",
            evidence("src/index.ts", 23, 38))],
        "空依赖图符合当前无导入实现，但没有消除自由文本中的类型机制错误。")
    add("p6-rag-context-fix/cases/report-mitt-shared.json", "contributor", "partially_correct", "partial", "partial",
        [install, setup, mutable], "正确承认没读实现，仍下纯函数架构结论；关于补文档的建议不等于已确认仓库缺文档。")

    for mode in ("bm25", "hybrid-rerank"):
        add(f"p6-rag-quality/cases/{mode}--ai--nanoid-p4-unknown_business.json", "qa", "correct", "not_applicable", "adequate", [
            claim("不能从公开库源码回答公司昨日真实生产统计", "unverifiable",
                  "任务需要未提供的公司遥测；不声称库的单元测试能证明生产没有事件。")],
            "正确性标记评价拒答决策，不代表核实了生产指标；给出的源码引用与未知生产数量无直接证明关系。",
            abstention="appropriate")
        add(f"p6-rag-quality/cases/{mode}--dbader--schedule-04.json", "qa", "partially_correct", "partial", "adequate", [
            claim("无 tag 时返回 self.jobs 的浅拷贝", "supported", "self.jobs[:] 返回新列表。",
                  evidence("schedule/__init__.py", 122, 133)),
            claim("首句是的肯定了返回内部列表本身", "contradicted", "与随后而非内部列表本身矛盾。",
                  evidence("schedule/__init__.py", 130, 131))],
            "两种策略相同的生成矛盾；有源码、格式正确也不能判完全正确。", consistency="contradictory")

    def ext(task, mode, correctness, support, completeness, claims, notes, points, **kwargs):
        add(f"p6-d-external/cases/sweqa-{task}--{mode}.json", "qa", correctness, support, completeness,
            claims, notes, points=points, **kwargs)

    ascii_fact = claim("ensure_ascii 默认 True，非 ASCII 字符转义", "supported", "provider 注释和设置一致。",
                       evidence("src/flask/json/provider.py", 144, 148))
    ext("flask-001", "fixed-bm25", "correct", "supported", "partial", [ascii_fact],
        "回答正确但未解释 False 保留 Unicode；可补充 dumps 的参数传递和测试。",
        {"default_true_escapes": True, "false_preserves_unicode": False})
    ext("flask-001", "sdk-agent", "partially_correct", "partial", "partial", [ascii_fact,
        claim("所有 Unicode 字符转换为 ASCII 转义", "contradicted",
              "应限定非 ASCII 字符；ASCII 字符也属于 Unicode。句子还在 kwargs.setdefault( 处中断。",
              evidence("src/flask/json/provider.py", 144, 148))],
        "JSON 和 finish 状态成功，但回答文本未写完；属于语义不完整，不能依赖 schema 发现。",
        {"default_true_escapes": True, "false_preserves_unicode": False})
    ext("flask-002", "fixed-bm25", "incorrect", "unsupported", "insufficient", [
        claim("标志用于让所有延迟函数仅执行一次", "contradicted",
              "标志保护注册后的 setup 修改；record_once/first_registration 控制特定回调只执行一次，二者不同。",
              evidence("src/flask/sansio/blueprints.py", 213, 244), evidence("src/flask/sansio/blueprints.py", 319, 335))],
        "核心机制混淆，即使检索已提供正确检查函数。", {"blocks_late_setup": False, "prevents_inconsistent_registration": False})
    ext("flask-002", "sdk-agent", "unverifiable", "not_applicable", "insufficient", [
        claim("当前工具上下文不足以解释注册标志", "unverifiable",
              "实际只读到示例 app 初始化，未检索目标源码；仓库本身有答案。")],
        "拒答与已见上下文相称，但任务未解决，属于检索失败，不可算与正确答案等价。",
        {"blocks_late_setup": False, "prevents_inconsistent_registration": False}, abstention="appropriate")
    modular = claim("Blueprint 将静态资源服务模块化", "supported", "蓝图提供静态文件，独立 static_folder 与路由。",
                    evidence("docs/blueprints.rst", 19, 37), evidence("src/flask/blueprints.py", 82, 102))
    cache = claim("应用与蓝图保持默认缓存配置一致", "supported", "两者读取 SEND_FILE_MAX_AGE_DEFAULT 并将 max_age 传入 send_from_directory。",
                  evidence("src/flask/blueprints.py", 55, 102), evidence("src/flask/app.py", 281, 328))
    ext("flask-003", "fixed-bm25", "correct", "supported", "partial", [modular, cache],
        "文档和 CHANGES 引用可支持高层结论，不能因没引用实现就判错；仍缺具体钩子与调用链解释。",
        {"blueprint_static_separation": True, "shared_hook_and_config": False, "explicit_blueprint_max_age": False})
    ext("flask-003", "sdk-agent", "partially_correct", "partial", "partial", [modular,
        claim("static_url_path/static_folder 实现统一缓存", "unsupported",
              "这两个属性控制地址和目录，不控制缓存；引用的 make_setup_state 也没有缓存逻辑。",
              evidence("src/flask/sansio/scaffold.py", 248, 262), evidence("src/flask/blueprints.py", 55, 102))],
        "模块化方向正确，缓存依据缺失。", {"blueprint_static_separation": True, "shared_hook_and_config": False, "explicit_blueprint_max_age": False})
    proxy = claim("非 None 的 no_proxy 参数优先于环境 no_proxy", "supported", "仅当参数是 None 才从环境读取。",
                  evidence("src/requests/utils.py", 764, 771))
    nonempty = claim("非空代理字典表示未绕过代理且存在代理配置", "supported", "绕过时返回 {}，否则返回 getproxies，测试配置了 http_proxy。",
                     evidence("src/requests/utils.py", 816, 825), evidence("tests/test_utils.py", 267, 272))
    ext("requests-001", "fixed-bm25", "correct", "partial", "adequate", [proxy, nonempty],
        "核心两点正确；非空结果的 else 正文在 824–825，引用 822–823 只有绕过分支，但测试提供旁证。不是证明真实请求已走代理。",
        {"argument_precedes_environment": True, "nonempty_means_no_bypass_with_config": True})
    ext("requests-001", "sdk-agent", "partially_correct", "partial", "partial", [nonempty,
        claim("no_proxy 参数不覆盖环境变量优先级", "contradicted", "混淆 no_proxy 与 HTTP_PROXY；子函数明确检查参数并临时覆写 no_proxy。",
              evidence("src/requests/utils.py", 769, 771), evidence("src/requests/utils.py", 803, 813))],
        "只读包装函数，没追到 should_bypass_proxies；这是可定位的跨文件/跨函数补查缺口。",
        {"argument_precedes_environment": False, "nonempty_means_no_bypass_with_config": True})
    formats = claim("支持 HTML meta、pragma 与 XML 声明形式", "supported", "参数化样例展示这些正文字符串，每例恰好一个 UTF-8 结果。",
                    evidence("tests/test_utils.py", 368, 384))
    order = claim("多个声明按在正文中出现的顺序返回", "contradicted",
                  "测试 XML 出现在最前，返回却是 HTML5、HTML4、XML；实现按 regex 类别拼接结果。",
                  evidence("tests/test_utils.py", 386, 392), evidence("src/requests/utils.py", 493, 501))
    ext("requests-002", "fixed-bm25", "incorrect", "unsupported", "insufficient", [formats, order,
        claim("test_pragmas 验证大小写不敏感", "unsupported", "样例都使用 UTF-8，未混用大小写；XML regex 也未设 re.I。",
              evidence("tests/test_utils.py", 368, 384), evidence("src/requests/utils.py", 493, 501))],
        "未说明保留原值；题目中的 HTTP headers/normalize 前提应纠正，不能顺着错误前提编结论。",
        {"preserves_case": False, "one_encoding_per_fixture": False, "content_formats": True})
    ext("requests-002", "sdk-agent", "incorrect", "unsupported", "insufficient", [formats, order,
        claim("返回规范化后的 UTF-8 编码名", "contradicted", "findall 直接返回匹配值，没有大小写或别名规范化步骤。",
              evidence("src/requests/utils.py", 493, 501))],
        "读到 test_precedence 仍解释成文档顺序，是理解错误，不是缺失源码。",
        {"preserves_case": False, "one_encoding_per_fixture": False, "content_formats": True})
    ext("requests-003", "fixed-bm25", "partially_correct", "partial", "partial", [
        claim("InvalidSchema 同属 RequestException 与 ValueError", "supported", "双重继承声明明确，可按任一异常家族捕获。",
              evidence("src/requests/exceptions.py", 99, 108)),
        claim("http:// 缺 host 属于 InvalidSchema", "contradicted", "测试和 prepare_url 都给出 InvalidURL。",
              evidence("tests/test_requests.py", 97, 111), evidence("src/requests/models.py", 437, 444))],
        "偏题比较 MissingSchema；只粗略解释父类，缺 RequestException 的 IOError/请求元数据机制。",
        {"dual_exception_categories": True, "request_exception_details": False, "correct_raise_context": False})
    ext("requests-003", "sdk-agent", "unverifiable", "unverifiable", "unverifiable", [
        claim("没有可交付结构化答案", "unverifiable", "原始完成状态为 truncated，保留原响应，不从半成品补出成功答案。")],
        "生成失败单独计数；不以额外生成掩盖失败。", None, abstention="unverifiable", scope="unverifiable", consistency="unverifiable")

    assert set(judgments) == set(candidates), "Every unique candidate needs an explicit authored judgment"
    ordered = [judgments[cid] for cid in sorted(judgments)]
    frozen(OUT / "judgments.json", ordered)
    frozen(OUT / "semantic-summary.json", summarize_reviews(ordered, identities))
    print(json.dumps({"reviewed_unique_candidates": len(ordered), "claims": sum(len(r['claims']) for r in ordered),
                      "paid_calls": 0, "human_approved": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
