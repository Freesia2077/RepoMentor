"""Offline, source-bound review of the completed quality-v1 pilot. No generation."""

import hashlib
import json
import sqlite3

from p63_dataset import ROOT

from repomentor.config import load_chat_settings
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import verify_spans
from repomentor.records import write
from repomentor.report_validation import inspect_report_locations
from repomentor.repository import SnapshotStore
from repomentor.semantic_review import validate_judgment

OUT = ROOT / "experiments/p63-quality"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def claim(text, status, reason, path, start, end):
    return {"claim": text, "status": status, "rationale": reason,
            "evidence": [{"path": path, "start_line": start, "end_line": end}]}


def decision(kind):
    # These are Codex's explicit judgments of the saved candidates, not keyword scoring.
    if kind == "explorer":
        return "partially_correct", "partial", "adequate", "consistent", [
            claim("轻量 TypeScript 事件库，入口和构建工具", "supported",
                  "package 的 source/scripts 与 README 的用途描述支持主要概览。", "package.json", 1, 27),
            claim("无法确认 Map 机制是因为未读 index.ts 以外源码", "unsupported",
                  "Map 实现就在 index.ts 第 52 行。缺口归因为未读其他文件会误导后续导航。", "src/index.ts", 46, 71),
        ]
    if kind == "mentor":
        return "partially_correct", "partial", "partial", "consistent", [
            claim("mitt 实例化映射，on/off 修改处理器，emit 支持通配符", "partial",
                  "核心机制大致正确，但把接口声明当实现依据；Map 第 52 行未进最终输入。", "src/index.ts", 46, 71),
            claim("src/index.ts L189–202 是通配符测试", "contradicted",
                  "此范围属于 test/index_test.ts；src/index.ts 总长不足该行号。路径合法不等于引用支持。", "test/index_test.ts", 189, 202),
            claim("采用纯函数式设计", "partial",
                  "方法不依赖 this 不等于纯函数；on 会修改 Map/数组。", "src/index.ts", 66, 73),
        ]
    if kind == "contributor":
        return "partially_correct", "partial", "partial", "consistent", [
            claim("npm run build/npm test/npm run lint 为声明命令", "supported",
                  "scripts 可核验；这不代表已实际运行目标仓库测试。", "package.json", 18, 27),
            claim("package.json L41–46 提供使用代码", "contradicted",
                  "实际是 license/files/mocha 配置，不能支持使用示例。", "package.json", 41, 46),
            claim("贡献建议是缺少 src/index.ts 或 tsconfig.json", "unsupported",
                  "将模型上下文缺口包装为仓库贡献项；未形成具体建议，源码实际存在。", "src/index.ts", 46, 71),
        ]
    if kind == "history-dev-05":
        return "partially_correct", "partial", "adequate", "contradictory", [
            claim("不传 tag 返回列表本身：首句回答是的，随后解释为副本", "contradicted",
                  "self.jobs[:] 明确创建浅拷贝。后半句正确，但开头对题干的肯定与其矛盾。", "schedule/__init__.py", 122, 133),
        ]
    assert kind == "sweqa-requests-001"
    return "correct", "partial", "adequate", "consistent", [
        claim("显式 no_proxy 优先于环境变量", "partial",
              "结论正确；实际引用只有外层委派和返回分支，优先级需要引用 769–771。", "src/requests/utils.py", 769, 771),
        claim("非空返回意味着未走 bypass 返回空字典分支", "supported",
              "822–825 支持此分支解释；不能由此保证网络代理请求实际成功。", "src/requests/utils.py", 816, 825),
    ]


def audit():
    candidates, judgments, locations = [], [], []
    store = SnapshotStore(data_root() / "corpus")
    for path in sorted((OUT / "cases").glob("*.json")):
        case = load(path)
        reader = store.open(case["snapshot_id"])
        records = case.get("stages") or [case["result"]]
        for record in records:
            stage = record.get("stage", "answer")
            context = record.get("final_context", record["context"])
            for spans in (context, record["context"], (record.get("initial_retrieval") or {}).get("sources", [])):
                verify_spans(reader, spans)
            answer = case["report"]["outputs"][stage] if stage != "answer" else record["answer"]
            candidate = {"candidate_id": case["case_id"] + "/" + stage, "answer": answer,
                         "observed_context": context, "snapshot_id": case["snapshot_id"],
                         "source_file": path.relative_to(ROOT).as_posix(),
                         "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            correctness, support, completeness, consistency, claims = decision(
                stage if stage != "answer" else case["task_id"])
            judgment = validate_judgment({"candidate_id": candidate["candidate_id"],
                "correctness": correctness, "citation_support": support, "completeness": completeness,
                "consistency": consistency, "entry_scope": "correct", "abstention": "not_applicable",
                "claims": claims, "notes": "已核对发布输出及固定快照；开发样本非盲评审，不估计总体准确率。"}, candidate, reader)
            judgment["solved"] = (record["completion"]["status"] == "success" and correctness == "correct"
                                   and support == "supported" and completeness == "adequate" and consistency == "consistent")
            candidates.append(candidate)
            judgments.append(judgment)
            if stage != "answer":
                locations.append({"candidate_id": candidate["candidate_id"],
                                  **inspect_report_locations(reader, answer, context)})
    with sqlite3.connect((runtime_dir() / "p63/dispatches.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM dispatches ORDER BY ordinal")]
    pilot = [r for r in rows if r["case"].startswith("quality-v1/")]
    assert len(pilot) == 24 and len(rows) <= 600
    assert all(not r["paid"] and r["budget"]["fits"] and
               r["budget"]["total"] <= r["budget"]["context_window"] in (8192, 16384) for r in rows)
    frozen = load(ROOT / "experiments/p63/completed-files.json")["sha256"]
    assert all(hashlib.sha256((ROOT / "experiments/p63" / p).read_bytes()).hexdigest() == h for p, h in frozen.items())
    probes = [load(OUT / f"probes/source-{n}.json") for n in (11000, 14000)]
    assert all(p["accepted"] and p["hardware_context_verified"] for p in probes)
    settings = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    assert settings.context_window == 16384 and settings.max_tokens == 1536
    assert load_chat_settings(ROOT / "config/model-settings.qwen-8k.json").context_window == 8192
    # Re-running this source audit must never apply these manual judgments to changed outputs.
    for name, values in (("review-candidates.json", candidates), ("judgments.json", judgments)):
        path = OUT / name
        if path.exists():
            assert load(path) == values, f"review binding changed; requires a new explicit review: {name}"
        else:
            write(path, values)
    write(OUT / "location-audit.json", locations)
    write(OUT / "audit.json", {"generation_requests": 0, "global_reserved_requests": len(rows),
        "pilot_requests": len(pilot), "paid_requests": 0, "reviewed_candidates": len(judgments),
        "baseline_frozen_files_verified": len(frozen), "source_ranges_verified": True,
        "budget_checks_passed": True, "human_approved": False, "training_eligible": False,
        "semantic_quality_accepted": False})
    print(json.dumps({"reviewed": len(judgments), "pilot": len(pilot), "global": len(rows), "generation": 0}))


if __name__ == "__main__":
    audit()
