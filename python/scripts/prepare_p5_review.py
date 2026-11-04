"""Link explicitly authored, nonblind diagnostic observations to unchanged model outputs."""

import json
from pathlib import Path

from repomentor.evaluation import atomic_write

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/p5"
OBSERVATIONS = {
    "developit--mitt-p4-cross_map": {
        "syntax_bm25": "主结论错误：传入 Map 时实际复用它，回答却声称新建内部 Map。",
        "syntax_hybrid_rerank": "仍不合格：同一句先说不会复用，再说直接引用传入 Map，前后矛盾。源码覆盖改善没有自动解决推理问题。",
    },
    "developit--mitt-p4-cross_case": {
        "syntax_bm25": "主结论错误：回答称统一转小写，引用的测试却明确区分大小写。合法引用范围没有保证蕴含关系。",
        "syntax_hybrid_rerank": "主结论得到纠正：明确不归一化大小写。仍未按题意同时解释实现和测试，不能据此给完整性满分。",
    },
    "ai--nanoid-p4-cross_custom": {
        "syntax_bm25": "主结论错误：默认长度在 index.js:96 明确为 21；生成器覆盖长度使用其第一个参数，回答的第二个参数表述混淆工厂与返回函数。",
        "syntax_hybrid_rerank": "部分纠正：默认长度 21 正确；但漏掉 test/index.test.js:211-212 的 nanoidA(10) 案例，混淆覆盖长度与零长度边界测试。",
    },
    "developit--mitt-p4-unknown_runtime": {
        "syntax_bm25": "合理拒答，没有编造用户本机测试统计；CI 配置引用不构成真实运行结果。",
        "syntax_hybrid_rerank": "合理拒答，但只有无法回答，缺少说明需要用户本机测试输出，教学价值有限。",
    },
}


def main():
    tasks = {
        r["task_id"]: r
        for r in [
            json.loads(line)
            for line in (ROOT / "experiments/p4/tasks.jsonl").read_text(encoding="utf-8").splitlines()
        ]
    }
    rows = [json.loads(line) for line in (EXP / "generation.jsonl").read_text(encoding="utf-8").splitlines()]
    cases = []
    for row in rows:
        note = OBSERVATIONS.get(row["task_id"], {}).get(row["mode"])
        if note:
            result, task = row["result"], tasks[row["task_id"]]
            cases.append(
                {
                    "source": "generation.jsonl",
                    "task_id": row["task_id"],
                    "condition": row["mode"],
                    "attempt_id": result["attempt_id"],
                    "question": task["input"]["question"],
                    "raw_answer": result["answer"],
                    "gold_reference": task["gold"],
                    "observation": note,
                }
            )
    for filename, note in {
        "cli-chat-turn1.json": "回答以是的开头，未明确区分清空监听器数组与删除 Map 键；src/index.ts:88 使用 set(type, [])，没有 delete。",
        "cli-chat-turn2-recovered.json": "恢复的流程成功，但回答把传入不存在的处理器错读为不传处理器，没有解释 indexOf(-1) >>> 0 防止 splice(-1,1) 误删最后一项。",
        "cli-chat-turn3.json": "回答说键可能保留，表达仍不精确；当前实现明确保留键并写入空数组。不能用格式成功掩盖事实表述问题。",
    }.items():
        row = json.loads((EXP / filename).read_text(encoding="utf-8"))
        cases.append(
            {
                "source": filename,
                "attempt_id": row["result"]["attempt_id"],
                "question": row["question"],
                "raw_answer": row["result"]["answer"],
                "source_reference": "developit/mitt@6b41670516ed8e8b738612f60491995470aa63b3 src/index.ts:82-91",
                "observation": note,
            }
        )
    record = {
        "reviewer": "Codex",
        "method": "Nonblind diagnostic source inspection, selected cases; not a representative semantic benchmark",
        "human_approved": False,
        "aggregate_accuracy": None,
        "training_use": "Forbidden: these are validation cases",
        "cases": cases,
    }
    atomic_write(EXP / "semantic-case-review.json", json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({"diagnostic_cases": len(cases), "human_approved": False}))


if __name__ == "__main__":
    main()
