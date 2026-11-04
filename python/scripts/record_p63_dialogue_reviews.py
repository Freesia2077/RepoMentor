"""Source-read Codex reviews of actual continuous dialogue turns; no generated judges."""

from p63_dataset import OUT
from record_p63_reviews import assessment as claim
from record_p63_reviews import ref, verdict
from record_p63_v2_reviews import missing
from run_rag_quality import frozen


def main():
    mt, src = "test/index_test.ts", "src/index.ts"
    u, tests, provider = "src/requests/utils.py", "tests/test_utils.py", "src/flask/json/provider.py"
    rows = {
        "nanoid-entry--turn-1/answer": missing("首轮研究输出截断；后三轮阻塞，没有伪造关联答案。"),
        "mitt-case--turn-1/answer": verdict("correct", "supported", "adequate", "不转小写的结论由真实测试断言支持。",
            claim("on 不转小写", "supported", "FOO/Bar 原样保留，小写键不存在。", ref(mt, 79, 90))),
        "mitt-case--turn-2/answer": verdict("correct", "supported", "adequate", "沿用 on 目标，说明不同大小写视为不同键，并引用真实断言。",
            claim("测试验证不同大小写不合并", "supported", "断言原始键存在、小写键不存在。", ref(mt, 79, 90))),
        "mitt-case--turn-3/answer": verdict("correct", "supported", "adequate", "省略 handler 时清空该类型全部处理器，set 空数组及文档支持。",
            claim("清空该类型处理器", "supported", "all.set(type, [])。", ref(src, 82, 91))),
        "mitt-case--turn-4/answer": verdict("partially_correct", "partial", "adequate", "后半句不删除键正确，但对是否删除开头答“会”，前后矛盾。",
            claim("先说会删键，后说不会删键", "partial", "实际只清空数组而保留键。", ref(src, 82, 91)), consistency="contradictory"),
        "requests-proxy--turn-1/answer": verdict("correct", "partial", "adequate", "bypass True/False 两分支描述正确，但只引用 True 分支，漏掉返回 getproxies 的 825 行。",
            claim("True 返回空字典，否则 getproxies", "supported", "完整函数支持，引用范围缺 False 分支。", ref(u, 816, 825))),
        "requests-proxy--turn-2/answer": verdict("correct", "supported", "adequate", "追问补查到了实际 None 条件，显式参数优先正确且有直接引用。",
            claim("显式 no_proxy 优先", "supported", "只有 no_proxy is None 时才读环境。", ref(u, 769, 771))),
        "requests-proxy--turn-3/answer": verdict("partially_correct", "partial", "partial", "非空断言是真实的，但实际函数名是 test_not_bypass，沿用了题目错误名称而未纠正。",
            claim("非空字典断言验证未绕过", "supported", "test_not_bypass 确有这个断言。", ref(tests, 242, 243)),
            claim("断言所属测试名为 test_get_environ_proxies", "contradicted", "所引函数实际名为 test_not_bypass。", ref(tests, 242, 243))),
        "requests-proxy--turn-4/answer": verdict("correct", "partial", "partial", "不能证明线上可达是正确的，但未解释只检查代理配置、没有网络连通性测量；单独断言引用不足。",
            claim("测试不能证明线上代理可达", "supported", "get_environ_proxies 仅判断绕过并返回配置，无连通请求。", ref(u, 816, 825), ref(tests, 242, 243)), abstention="appropriate"),
        "flask-json--turn-1/answer": verdict("correct", "supported", "adequate", "默认 True，引用定义与说明。",
            claim("ensure_ascii 默认为 True", "supported", "属性赋值支持。", ref(provider, 144, 148))),
        "flask-json--turn-2/answer": verdict("correct", "supported", "adequate", "跟进开关 False 的 Unicode 行为正确，沿用正确目标。",
            claim("False 时保留原 Unicode 字符", "supported", "属性说明支持；对应测试还比较真实雪人字符。", ref(provider, 144, 148), ref("tests/test_json.py", 48, 54))),
        "flask-json--turn-3/answer": verdict("correct", "supported", "adequate", "准确定位测试名称并引用开关两种参数与断言。",
            claim("test_json_as_unicode 验证开关", "supported", "参数 True/False，赋值后序列化并比较预期。", ref("tests/test_json.py", 48, 54))),
        "flask-json--turn-4/answer": verdict("correct", "supported", "partial", "切换目标后引用了 dump 和 dumps，写入/字符串区别正确；问的是返回行为，未明确 dump 返回 None，且引用 dumps 为具体默认实现。",
            claim("dump 写文件，具体 dumps 返回字符串", "supported", "fp.write(self.dumps(...)) 和 DefaultJSONProvider.dumps 支持。", ref(provider, 49, 57), ref(provider, 166, 179))),
    }
    frozen(OUT / "runs/dialogues-v1/review-decisions.json", rows)


if __name__ == "__main__":
    main()
