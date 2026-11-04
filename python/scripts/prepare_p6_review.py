"""Persist Codex's nonblind source diagnoses; never turn them into human labels or training data."""

import json

from p6_archive import OUT, read_jsonl, verify_archive

NOTES = {
    "developit--mitt-p4-cross_map": {
        "syntax_bm25": ("incorrect", "错误地说会新建 Map。源码 all = all || new Map() 复用传入对象。"),
        "syntax_hybrid_rerank": (
            "contradictory",
            "不会复用与直接引用传入 Map 自相矛盾；未解释 on 测试观察外部 events Map。",
        ),
        "agent_syntax_bm25": (
            "contradictory",
            "虽读到实现与测试，仍说不会复用但直接引用；没有修复主要语义错误。",
        ),
    },
    "developit--mitt-p4-cross_case": {
        "syntax_bm25": ("incorrect", "把 NOT normalize case 误读为转换小写。"),
        "syntax_hybrid_rerank": (
            "main_conclusion_supported",
            "不转换大小写的主要结论正确；引用只指测试，缺少实现层解释。",
        ),
        "agent_syntax_bm25": (
            "main_conclusion_supported",
            "主要结论正确，同时引用实现与测试；仍较简短，未逐条解释断言。",
        ),
    },
    "ai--nanoid-p4-cross_custom": {
        "syntax_bm25": ("incorrect", "错误地说无固定默认长度；源码默认21。"),
        "syntax_hybrid_rerank": (
            "partial_with_error",
            "默认21正确，但把覆盖长度测试解释为不传第二参数或传0；changes size 实际调用返回函数 nanoidA(10)。",
        ),
        "agent_syntax_bm25": (
            "partial_with_error",
            "找到 non-secure 的默认21（真实替代来源，不能只因偏离 gold 判错）；但混合 Node 测试，未区分创建函数时设置默认长度与返回函数调用时覆盖长度。",
        ),
    },
    "ai--nanoid-p4-cross_negative": {
        "syntax_bm25": ("incomplete_abstention", "没有回答源码中可确认的负数抛错行为。"),
        "syntax_hybrid_rerank": (
            "unsupported_generalization",
            "Node 抛错部分正确；把 non-secure 返回空串泛化到 browser 错误。index.browser.js:77 构造负长度 Uint8Array，不是空串路径。",
        ),
        "agent_syntax_bm25": (
            "unsupported_generalization",
            "同样把 non-secure 与浏览器混淆，缺少 RangeError 与实际 nanoid(-10) 断言说明。",
        ),
    },
    "dbader--schedule-01": {
        "syntax_bm25": (
            "main_conclusion_supported",
            "不补跑的主要结论正确；未解释每次只执行符合 should_run 的任务一次。",
        ),
        "syntax_hybrid_rerank": ("main_conclusion_supported", "主要结论正确，解释较简短。"),
        "agent_syntax_bm25": (
            "main_conclusion_supported",
            "主要结论正确，未见超越固定 RAG 的教学收益，却增加调用。",
        ),
    },
    "more-itertools--more-itertools-01": {
        "syntax_bm25": ("main_conclusion_supported", "返回列表正确；可补 list(islice(...)) 原因。"),
        "syntax_hybrid_rerank": ("main_conclusion_supported", "返回列表正确，只有极短答案。"),
        "agent_syntax_bm25": ("main_conclusion_supported", "返回列表正确，114行文档直接支持；未补执行机制。"),
    },
    "developit--mitt-p4-unknown_runtime": {
        mode: (
            "appropriate_abstention",
            "没有编造本机运行结果；Agent 的无法回答过短，应指出缺少运行日志，且进行了4次不必要的源码工具调用。",
        )
        for mode in ("syntax_bm25", "syntax_hybrid_rerank", "agent_syntax_bm25")
    },
    "ai--nanoid-p4-unknown_business": {
        mode: (
            "appropriate_abstention",
            "没有编造生产统计；应说明需要业务日志/监控。Agent 花满6次工具预算仍只能拒答，停止策略需要改进。",
        )
        for mode in ("syntax_bm25", "syntax_hybrid_rerank", "agent_syntax_bm25")
    },
}


def main():
    packet = read_jsonl(OUT / "review-packet.jsonl")
    diagnoses = [
        {
            "task_id": r["task_id"],
            "mode": r["mode"],
            "attempt_id": r["attempt_id"],
            "verdict": NOTES[r["task_id"]][r["mode"]][0],
            "note": NOTES[r["task_id"]][r["mode"]][1],
            "human_approved": False,
        }
        for r in packet
    ]
    for i, note in [
        (1, "大小写结论正确；同矩阵主要结果。"),
        (
            2,
            "off 名称及不会误删正确，但会抛出错误错误。indexOf 未找到是-1，>>>0变为大正整数，splice不删除元素；引用的测试也没有证明抛错。",
        ),
        (
            3,
            "错误地说删除 Map 键。src/index.ts:88 是 set(type, [])，test/index_test.ts:148-156 验证 get 返回空数组。真实对话延续并没有保证事实正确。",
        ),
    ]:
        r = json.loads((OUT / f"cli-turn{i}.json").read_text(encoding="utf-8"))["result"]
        diagnoses.append(
            {
                "task_id": f"cli-turn{i}",
                "mode": "agent_syntax_bm25",
                "attempt_id": r["attempt_id"],
                "verdict": "main_conclusion_supported" if i == 1 else "incorrect",
                "note": note,
                "human_approved": False,
            }
        )
    recorded = json.loads((OUT / "semantic-diagnostics.json").read_text(encoding="utf-8"))
    expected = (
        {
            "reviewer": "Codex",
            "method": "Nonblind source comparison, authored during development; not an independent human or judge-model evaluation",
            "human_approved": False,
            "aggregate_answer_accuracy": None,
            "training_eligible": False,
            "cases": diagnoses,
            "next_review": "Independent review of factual correctness, completeness, citation entailment, abstention and learning value (0/1/2); task_success remains null in review-packet.",
        },
    )
    assert recorded == expected[0]
    print(json.dumps({**verify_archive(), "nonblind_diagnostic_cases": len(diagnoses)}))


if __name__ == "__main__":
    main()
