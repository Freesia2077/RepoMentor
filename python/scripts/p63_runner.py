"""One P6.3 entry: preflight, retrieval, run/resume, replay, review export and comparison."""

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from p63_dataset import OUT, ROOT, prepare
from run_rag_quality import frozen, profile, sha

from repomentor.agent_tools import AgentLimits
from repomentor.conversation import ConversationStore
from repomentor.experiment_ledger import RequestLedger
from repomentor.paths import data_root, runtime_dir
from repomentor.presentation import render_run
from repomentor.rag import RagEngine, verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version
from repomentor.sdk_collaboration import CollaborationSettings
from repomentor.sdk_report import SdkReport
from repomentor.source_windows import source_span


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def settings():
    return profile().model_copy(update={"max_tokens": 1536, "stream": False})


def ledger(protocol):
    return RequestLedger(runtime_dir() / "p63/dispatches.sqlite", limit=protocol["max_local_requests"],
                         experiment=fingerprint(protocol))


def verify_frozen(protocol):
    for path, digest in protocol["protected_files"].items():
        assert sha(ROOT / path) == digest, path
    for path, digest in protocol["tasks_sha256"].items():
        assert sha(OUT / path) == digest, path


def open_task(task):
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    assert reader.snapshot.commit_sha == task["commit_sha"]
    return reader


def reference_sources(reader, refs):
    spans = []
    for ref in refs:
        text, cut = reader.read(ref["path"], 500_000)
        assert not cut and 1 <= ref["start_line"] <= ref["end_line"] <= len(text.splitlines())
        spans.append(source_span(ref["path"], text.splitlines(), ref["start_line"], ref["end_line"]))
    verify_spans(reader, spans)
    return spans


def fixtures():
    from finalize_d_review import GOLD

    tasks = load(OUT / "development.json")
    refs = {k: {"sources": v} for k, v in GOLD.items()}
    historical = [
        [("index.js", 133, 138), ("index.js", 177, 177), ("test/index.test.js", 87, 93)],
        [("index.js", 133, 138), ("index.js", 177, 177)],
        [("non-secure/index.js", 19, 29)],
        [("src/index.ts", 66, 73)],
        [("schedule/__init__.py", 122, 133)], [],
    ]
    for i, spans in enumerate(historical, 1):
        refs[f"history-dev-{i:02}"] = {"sources": [dict(path=p, start_line=s, end_line=e) for p, s, e in spans]}
    for task in tasks:
        reference_sources(open_task(task), refs[task["task_id"]]["sources"])
    frozen(OUT / "evaluation-only/development.json", refs)
    repos = {t["repository"]: t for t in tasks}
    conversations = [
        ("nanoid-entry", "ai/nanoid", [
            "Node 默认 nanoid 对负数长度如何处理？", "那么它的测试如何验证这个行为？",
            "切换到 non-secure 入口，nanoid(-10) 也会抛错吗？", "那么这个入口是把负数改成零，还是循环根本没有执行？"]),
        ("mitt-case", "developit/mitt", [
            "mitt 的 on 会把事件名转成小写吗？", "那么对应测试如何验证大小写？",
            "off 不传 handler 怎么处理已经注册的处理器？", "那么这个操作会删除 all 中的事件键吗？"]),
        ("requests-proxy", "psf/requests", [
            "get_environ_proxies 如何调用 should_bypass_proxies？", "那么显式 no_proxy 参数与环境变量哪个优先？",
            "test_get_environ_proxies 用哪个断言验证未绕过代理？", "这个测试能证明线上代理服务器当前可达吗？"]),
        ("flask-json", "pallets/flask", [
            "DefaultJSONProvider.ensure_ascii 默认是什么？", "那么设为 False 后 Unicode 会怎样输出？",
            "哪个测试验证 ensure_ascii 的开关？", "切换到 JSONProvider.dump，它和 dumps 的返回行为相同吗？"]),
    ]
    frozen(OUT / "dialogues.json", [{"dialogue_id": name, "repository": repo,
        "snapshot_id": repos[repo]["snapshot_id"], "commit_sha": repos[repo]["commit_sha"], "questions": qs}
        for name, repo, qs in conversations])
    frozen(OUT / "supplement-protocol.json", {
        "development_refs_sha256": sha(OUT / "evaluation-only/development.json"),
        "dialogues_sha256": sha(OUT / "dialogues.json"),
        "oracle_task_ids": [t["task_id"] for t in tasks[:6]],
        "reports": ["developit/mitt", "ai/nanoid"],
        "review": "Codex nonblind model review; all tasks excluded from training", "human_approved": False})


def preflight(protocol):
    verify_frozen(protocol)
    fixtures()
    rows = []
    unique = {t["snapshot_id"]: t for t in load(OUT / "development.json") + load(OUT / "confirmation.json")}
    for task in unique.values():
        print(json.dumps({"preflight": task["repository"]}), flush=True)
        reader = open_task(task)
        started = time.perf_counter()
        index = build_index(reader)
        rows.append({"repository": task["repository"], "snapshot_id": task["snapshot_id"],
                     "index_id": index.index_id, "chunks": len(index.chunks),
                     "seconds": round(time.perf_counter()-started, 4),
                     "source_index_omissions": index.record.get("omitted", index.record.get("omissions", [])),
                     "symbol_coverage": {k: v for k, v in index.symbols.record.items()
                                         if k not in {"definitions", "relations", "paths"}}})
        print(json.dumps({k: v for k, v in rows[-1].items() if k not in {"symbol_coverage", "source_index_omissions"}}), flush=True)
    write(OUT / "preflight.json", {"sdk": sdk_version(), "profile": settings().public(), "indexes": rows,
        "generation_requests": 0, "protected_files_verified": len(protocol["protected_files"])})


def runtime(mode, protocol, case, replay=False):
    store = ledger(protocol)

    def dispatch(body, budget):
        if replay:
            raise AssertionError("replay forbids generation")
        store.reserve(settings(), case, body, budget)
        store.export(OUT / "dispatches.json")

    class Fixed(ChatCompletionsRuntime):
        async def verify_backend(self):
            assert not replay, "replay forbids metadata requests"
            return await super().verify_backend()

    class Sdk(OpenAISdkRuntime):
        async def verify_backend(self):
            assert not replay, "replay forbids metadata requests"
            return await super().verify_backend()

    return (Fixed if mode in {"fixed", "oracle"} else Sdk)(settings(), before_dispatch=dispatch)


def identity(args):
    return {"version": "p63-run-v1", "sdk": sdk_version(), "profile": settings().public(),
            "retriever": args.retriever, "split": args.split, "modes": args.modes,
            "limits": AgentLimits(max_tool_calls=6, max_decisions=8, timeout_seconds=360).model_dump(),
            "collaboration": "auto" if args.experts else "off", "device": args.device,
            "tasks_sha256": sha(OUT / (args.split + ".json")),
            "runner_sha256": sha(Path(__file__)), "automatic_retry": False}


async def run(protocol, args):
    verify_frozen(protocol)
    manifest = identity(args)
    folder = OUT / "runs" / args.batch
    if args.split == "confirmation":
        selection = load(OUT / "selection.json")
        assert manifest["sdk"] == selection["sdk"] and args.retriever == selection["retriever"]
        assert not args.experts and set(args.modes) == {"fixed", "sdk"}
    frozen(folder / "manifest.json", manifest)
    tasks = load(OUT / (args.split + ".json"))
    if args.oracle:
        ids = load(OUT / "supplement-protocol.json")["oracle_task_ids"]
        tasks = [t for t in tasks if t["task_id"] in ids]
    if args.only:
        assert args.split == "development", "confirmation cannot be cherry-picked"
        tasks = [t for t in tasks if t["task_id"] in args.only]
    indexes = {}
    receipts = []
    for task in tasks:
        reader = open_task(task)
        if task["snapshot_id"] not in indexes:
            indexes[task["snapshot_id"]] = build_index(reader, retriever=args.retriever, device=args.device, profile=settings())
        index = indexes[task["snapshot_id"]]
        for mode in args.modes:
            path = folder / "cases" / (task["task_id"] + "--" + mode + ".json")
            if path.exists() and not args.replay:
                continue  # Completed failures are also immutable; never retry until success.
            options = {}
            if task.get("depends_on"):
                parent = load(folder / "cases" / (task["depends_on"] + "--" + mode + ".json"))
                prior = parent["result"]
                if prior["answer"] is None:
                    write(path, {**task, "mode": mode, "blocked_by": task["depends_on"], "result": None})
                    continue
                options["history"] = [{"role": "user", "content": parent["input"]["question"]},
                    {"role": "assistant", "content": prior["answer"]["answer"]}]
                options["query"] = task["input"]["question"] + "\n追问对象：" + parent["input"]["question"]
                if mode == "sdk":
                    options["task_context"] = {"entry": prior.get("entry_focus", {}).get("label")}
            local = runtime(mode, protocol, args.batch + "/" + path.stem, args.replay)
            if mode in {"fixed", "oracle"}:
                engine = RagEngine(reader, index, local, runtime_dir() / "p63/rag-cache")
                if mode == "oracle":
                    refs = load(OUT / "evaluation-only/development.json")[task["task_id"]]["sources"]
                    options.update(mode="oracle", oracle_context=reference_sources(reader, refs))
            else:
                engine = SdkAgentEngine(reader, index, local, runtime_dir() / "p63/sdk-cache",
                    AgentLimits(**manifest["limits"]), collaboration=CollaborationSettings(mode=manifest["collaboration"]))
            result = await engine.answer(task["input"]["question"], **options)
            verify_spans(reader, result.get("final_context", result["context"]))
            if args.replay:
                original = load(path)["result"]
                assert result["new_model_requests"] == 0 and result["answer"] == original["answer"]
                assert result["attempt_id"] == original["attempt_id"]
            else:
                write(path, {**task, "mode": mode, "result": result})
            receipt = {"task_id": task["task_id"], "mode": mode, "status": result["completion"]["status"],
                       "requests": result["new_model_requests"]}
            print(json.dumps(receipt), flush=True)
            receipts.append(receipt)
    if args.replay:
        write(folder / "replay.json", {"cases": receipts, "generation_requests": 0, "metadata_requests": 0})
    ledger(protocol).export(OUT / "dispatches.json")
    verify_frozen(protocol)


async def dialogues(protocol, args):
    folder = OUT / "runs" / args.batch
    frozen(folder / "manifest.json", {**identity(args), "suite": "continuous-dialogues",
                                      "dialogues_sha256": sha(OUT / "dialogues.json")})
    store = ConversationStore(runtime_dir() / "p63/conversations")
    for task in load(OUT / "dialogues.json"):
        reader = open_task(task)
        index = build_index(reader, retriever=args.retriever, device=args.device, profile=settings())
        sid = fingerprint([args.batch, task["dialogue_id"]])[:32]
        if not store.path(sid).exists():
            store.create(task["snapshot_id"], session_id=sid)
        for i, q in enumerate(task["questions"], 1):
            path = folder / "cases" / f"{task['dialogue_id']}--turn-{i}.json"
            if path.exists():
                continue
            if store.load(sid)["pending"]:
                write(path, {"dialogue_id": task["dialogue_id"], "turn": i, "question": q,
                             "result": None, "blocked_by": "previous_failed_turn"})
                continue
            engine = SdkAgentEngine(reader, index, runtime("sdk", protocol, args.batch + "/" + path.stem),
                runtime_dir() / "p63/sdk-cache", AgentLimits(timeout_seconds=360))
            row = await store.turn(sid, engine, q, request_id=path.stem)
            write(path, {**task, "turn": i, "session_id": sid, **row})
            print(json.dumps({"dialogue": task["dialogue_id"], "turn": i,
                              "status": row["result"]["completion"]["status"]}), flush=True)
        write(folder / (task["dialogue_id"] + "-session.json"), store.load(sid))
    ledger(protocol).export(OUT / "dispatches.json")


async def reports(protocol, args):
    folder = OUT / "runs" / args.batch
    frozen(folder / "manifest.json", {**identity(args), "suite": "three-stage-reports"})
    tasks = {t["repository"]: t for t in load(OUT / "development.json")}
    for repo in load(OUT / "supplement-protocol.json")["reports"]:
        path = folder / "cases" / (repo.replace("/", "--") + ".json")
        if path.exists():
            continue
        task, reader = tasks[repo], open_task(tasks[repo])
        index = build_index(reader, retriever=args.retriever, device=args.device, profile=settings())
        report = SdkReport(reader, index, runtime("sdk", protocol, args.batch + "/" + path.stem),
                           runtime_dir() / "p63/sdk-cache", limits=AgentLimits(max_decisions=5, max_tool_calls=4, timeout_seconds=360))
        result = await report.run(total_timeout=1080)
        write(path, {**task, "report": result.model_dump(mode="json"), "stages": report.records})
        print(json.dumps({"report": repo, "status": result.status, "stages": len(report.records)}), flush=True)
    ledger(protocol).export(OUT / "dispatches.json")


def retrieval(protocol, args):
    from finalize_d_review import GOLD

    tasks = [json.loads(line) for line in (ROOT / "experiments/p4/tasks.jsonl").read_text(encoding="utf-8").splitlines() if line]
    tasks += [{**t, "gold": {"evidence": GOLD[t["task_id"]]}} for t in load(OUT / "development.json")[:6]]
    assert len(tasks) == 72
    grouped = defaultdict(list)
    for task in tasks:
        grouped[task["snapshot_id"]].append(task)
    for group in grouped.values():
        reader = open_task(group[0])
        started = time.perf_counter()
        index = build_index(reader, retriever=args.retriever, device=args.device, profile=settings())
        build_seconds = time.perf_counter() - started
        for task in group:
            path = OUT / "retrieval" / "current" / args.retriever / (task["task_id"] + ".json")
            if path.exists():
                assert load(path)["index_id"] == index.index_id, "retrieval implementation changed; archive earlier diagnostics"
                continue
            times, result = [], None
            for _ in range(2):
                started = time.perf_counter()
                result = index.search(task["input"]["question"], k=20)
                times.append(time.perf_counter() - started)
            spans = [r["chunk"] for r in result["candidates"]]
            refs = task["gold"]["evidence"]
            def overlaps(c, r):
                return c["path"] == r["path"] and max(c["start_line"], r["start_line"]) <= min(c["end_line"], r["end_line"])
            rank = next((i for i, c in enumerate(spans, 1) if any(overlaps(c, r) for r in refs)), None)
            metrics = {"recall_at_6": coverage(refs, spans[:6]), "recall_at_20": coverage(refs, spans),
                       "mrr": 1/rank if rank else 0 if refs else None}
            for k in (6, 20):
                metrics[f"target_overlap_recall_at_{k}"] = sum(any(overlaps(c, r) for c in spans[:k]) for r in refs)/len(refs) if refs else None
            needed = {(r["path"], n) for r in refs for n in range(r["start_line"], r["end_line"]+1)}
            actual = {(s["path"], n) for s in spans[:6] for n in range(s["start_line"], s["end_line"]+1)}
            metrics["line_coverage_at_6"] = len(needed & actual)/len(needed) if needed else None
            write(path, {"task_id": task["task_id"], "repository": task["repository"], "split": task["split"],
                "index_id": index.index_id, "base_index_id": index.base.index_id, "chunk_ids_sha256": fingerprint([c.chunk_id for c in index.chunks]),
                "retriever": args.retriever, "metrics": metrics, "first_query_seconds": times[0], "warm_query_seconds": times[1],
                "index_open_build_seconds": build_seconds, "chunks": len(index.chunks), "candidates": spans,
                "neural_work": dict(index.neural.stats) if hasattr(index, "neural") else {}, "generation_requests": 0})
            print(json.dumps({"retrieval": task["task_id"], "mode": args.retriever, "mrr": metrics["mrr"]}), flush=True)
        if hasattr(index, "release_models"):
            index.release_models()
    verify_frozen(protocol)


def export_review(args):
    folder = OUT / "runs" / args.batch
    rows = []
    for path in sorted((folder / "cases").glob("*.json")):
        case = load(path)
        records = [(s["stage"], s) for s in case.get("stages", [])] or [("answer", case.get("result"))]
        for stage, result in records:
            if result is None:
                continue
            cid = fingerprint([args.batch, path.stem, stage, result["attempt_id"]])[:24]
            rows.append({"candidate_id": cid, "source_file": path.relative_to(ROOT).as_posix(),
                "stage": stage, "record_sha256": fingerprint(result), "task_id": case.get("task_id", case.get("dialogue_id")),
                "snapshot_id": case["snapshot_id"], "mode": case.get("mode", "sdk"),
                "question": result.get("question", case.get("question")), "answer": result["answer"],
                "status": result["completion"]["status"], "visible_context": result.get("final_context", result["context"]),
                "quality": result.get("quality"), "reviewer": "Codex nonblind model review", "human_approved": False})
            if stage == "answer":
                target = folder / "markdown" / (path.stem + ".md")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(render_run(result), encoding="utf-8")
    write(folder / "review-candidates.json", rows)
    print(json.dumps({"review_candidates": len(rows), "batch": args.batch}))


def compare(protocol, args):
    folder = OUT / "runs" / args.batch
    cases = [load(p) for p in sorted((folder / "cases").glob("*.json"))]
    judgments = {j["candidate_id"]: j for j in load(folder / "judgments.json")} if (folder / "judgments.json").exists() else {}
    candidates = load(folder / "review-candidates.json") if (folder / "review-candidates.json").exists() else []
    by_source = {c["source_file"]: judgments.get(c["candidate_id"]) for c in candidates}
    groups = defaultdict(list)
    for case in cases:
        result = case.get("result")
        if result is not None:
            groups[case.get("mode", "sdk")].append(result)
    summary = {}
    for mode, results in groups.items():
        requests = [r.get("total_model_requests", r["new_model_requests"]) for r in results]
        summary[mode] = {"cases": len(results), "statuses": dict(Counter(r["completion"]["status"] for r in results)),
            "total_requests": sum(requests), "mean_requests": statistics.mean(requests),
            "elapsed_seconds": sum(r.get("elapsed_seconds", r["completion"]["elapsed_seconds"]) for r in results)}
    write(folder / "summary.json", {"modes": summary, "reviewed": len(judgments),
        "reviews": by_source, "semantic_correctness": "see individual judgments" if judgments else "not_scored",
        "paid_requests": 0, "global_reserved_requests": len(ledger(protocol).rows())})
    print(json.dumps(summary))
    from repomentor.experiment_review import import_reviews, summarize_batch
    if (folder / "review-decisions.json").exists():
        import_reviews(folder, ROOT)
    if (folder / "review-candidates.json").exists():
        summarize_batch(folder, ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "retrieval", "run", "resume", "replay", "oracle", "dialogues", "reports", "export-review", "compare", "verify"])
    parser.add_argument("--batch", default="development-v1")
    parser.add_argument("--split", choices=["development", "confirmation"], default="development")
    parser.add_argument("--modes", nargs="+", choices=["fixed", "sdk", "oracle"], default=["fixed", "sdk"])
    parser.add_argument("--retriever", choices=["bm25", "vector", "hybrid", "hybrid-rerank"], default="bm25")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--experts", action="store_true")
    args = parser.parse_args()
    if not args.batch.replace("-", "").replace("_", "").isalnum():
        parser.error("batch must be a simple directory name")
    protocol = prepare()
    args.replay, args.oracle = args.action == "replay", args.action == "oracle"
    if args.oracle:
        assert args.split == "development"
        args.modes = ["oracle"]
    if args.action == "preflight":
        preflight(protocol)
    elif args.action == "retrieval":
        retrieval(protocol, args)
    elif args.action in {"run", "resume", "replay", "oracle"}:
        asyncio.run(run(protocol, args))
    elif args.action in {"dialogues", "reports"}:
        asyncio.run((dialogues if args.action == "dialogues" else reports)(protocol, args))
    elif args.action == "export-review":
        export_review(args)
    elif args.action == "compare":
        compare(protocol, args)
    else:
        verify_frozen(protocol)
        ledger(protocol).export(OUT / "dispatches.json")
        print(json.dumps({"protected_files": len(protocol["protected_files"]), "frozen_tasks_verified": True}))


if __name__ == "__main__":
    main()
