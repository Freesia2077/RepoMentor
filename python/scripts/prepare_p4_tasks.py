"""Reuse P0 validation seeds; add source-reviewed cross-file and unavailable-information probes."""

import hashlib
import json
from pathlib import Path

from repomentor.config import write_json
from repomentor.evaluation import atomic_write
from repomentor.paths import data_root
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    seeds = [
        json.loads(x)
        for x in (ROOT / "experiments/p0/validation-seed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    corpus = {
        r["repository"]: r
        for r in json.loads((ROOT / "experiments/p0/corpus.json").read_text(encoding="utf-8"))["repositories"]
    }
    extras = [
        (
            "developit/mitt",
            "cross_map",
            "mitt 传入 Map 后是否复用？结合 src/index.ts 与 test/index_test.ts 的初始化和 on 测试说明。",
            [("src/index.ts", 46, 73), ("test/index_test.ts", 38, 61)],
            ["复用传入的 Map；on 对同一 Map 写入事件监听器；测试通过外部 events Map 观察注册结果。"],
            "cross_file",
        ),
        (
            "developit/mitt",
            "cross_case",
            "mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。",
            [("src/index.ts", 66, 73), ("test/index_test.ts", 79, 90)],
            ["不转换大小写；按原始 type 访问 Map；测试区分 FOO/foo、Bar/bar 等。"],
            "implementation_test",
        ),
        (
            "ai/nanoid",
            "cross_negative",
            "Node nanoid 负数长度如何处理，test/index.test.js 如何验证？",
            [("index.js", 136, 138), ("test/index.test.js", 87, 96)],
            [
                "负数长度抛出 RangeError；测试用 nanoid(-10) 并断言 Wrong ID size 或 Invalid typed array length。"
            ],
            "implementation_test",
        ),
        (
            "ai/nanoid",
            "cross_custom",
            "customAlphabet 的默认长度在哪里声明，测试如何验证可以覆盖长度？",
            [("index.js", 96, 98), ("test/index.test.js", 210, 213)],
            ["默认长度参数为21；测试 customAlphabet('a') 返回的函数传入10，得到10个a。"],
            "cross_file",
        ),
        (
            "developit/mitt",
            "unknown_runtime",
            "这个 mitt 仓库在我电脑上今天运行测试的实际通过数和耗时是多少？",
            [],
            ["固定源码不能证明用户电脑今天的测试运行结果，应说明无法确定。"],
            "unavailable_information",
        ),
        (
            "ai/nanoid",
            "unknown_business",
            "我们公司生产环境昨天用 nanoid 生成了多少个 ID，碰撞次数是多少？",
            [],
            ["源码不包含公司生产环境昨天的调用与碰撞统计，应说明无法确定。"],
            "unavailable_information",
        ),
    ]
    store = SnapshotStore(data_root() / "corpus")
    for repository, suffix, question, spans, facts, category in extras:
        meta = corpus[repository]
        reader = store.open(meta["snapshot_id"])
        evidence = []
        for path, start, end in spans:
            text, truncated = reader.read(path, 500_000)
            assert not truncated
            content = "\n".join(text.splitlines()[start - 1 : end])
            assert content and end <= len(text.splitlines())
            evidence.append(
                {
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
        seeds.append(
            {
                "task_id": repository.replace("/", "--") + "-p4-" + suffix,
                **{k: meta[k] for k in ("repository", "snapshot_id", "commit_sha", "split")},
                "task_type": category,
                "input": {"question": question, "history": []},
                "gold": {"required_facts": facts, "evidence": evidence},
                "review": {
                    "source_reviewed": True,
                    "human_approved": False,
                    "method": "Codex inspected frozen source; no target code executed",
                },
                "usage": "development diagnostic, not a blind final benchmark",
            }
        )
    atomic_write(
        ROOT / "experiments/p4/tasks.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in seeds)
    )
    write_json(
        ROOT / "experiments/p4/task-protocol.json",
        {
            "tasks": len(seeds),
            "p0_reused": 60,
            "new_source_reviewed": len(extras),
            "human_approved": False,
            "test_split_used": False,
            "gold_in_bm25_input": False,
            "metrics": "Full labeled line-span coverage at K; union across chunks allowed. Alternative evidence remains incomplete; human review pending.",
        },
    )
    print(json.dumps({"tasks": len(seeds), "model_calls": 0}))


if __name__ == "__main__":
    main()
