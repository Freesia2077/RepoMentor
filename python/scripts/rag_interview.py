"""Bounded current-version RAG baselines, anonymous review and simulated-use delivery."""

import argparse
import asyncio
import hashlib
import json
import random
import secrets
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from repomentor.config import load_chat_settings, write_json
from repomentor.experiment_ledger import RequestLedger
from repomentor.interview_baselines import (
    CONDITIONS,
    FrozenSourcesIndex,
    percentile,
    prepare_input,
    select_sources,
)
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import RagEngine, rag_version, verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, fingerprint
from repomentor.semantic_review import RUBRIC, validate_judgment
from repomentor.source_windows import source_span

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/rag-interview"
PRIVATE = runtime_dir() / "rag-interview"
CAP = 60

NEW = [
    (
        "developit/mitt",
        "branch",
        "仅按当前源码静态推导：若手动 emit('*', payload)，Map 的 '*' 处理器数组会被遍历几次？两轮实参有什么不同？注释是否把这种调用列为支持用法？",
        [
            "同一 '*' 列表被遍历两次，代码没有跳过普通分支",
            "第一轮 handler(payload)，第二轮 handler('*', payload)",
            "注释明确不支持手动触发 '*'，静态行为不等于稳定 API 保证",
        ],
        [("src/index.ts", 93, 121)],
    ),
    (
        "ai/nanoid",
        "entry",
        "在 non-secure 入口，customAlphabet('ab', 2.9) 返回的生成器分别不传 size、传 0、传 -1 时，长度是多少？解释位运算与循环条件，不要套用 Node 安全入口的异常规则。",
        [
            "长度依次为 2、0、0",
            "size | 0 将 2.9 转成 2，while(i-- > 0) 不进入零或负数循环",
            "此入口不按 Node 安全实现对负数抛 RangeError",
        ],
        [("non-secure/index.js", 5, 17)],
    ),
    (
        "dbader/schedule",
        "mutation",
        "Scheduler.run_all(delay_seconds=2) 执行某个任务时若该任务给 scheduler.jobs 追加新任务，新任务会在本轮也执行吗？延迟发生在每个任务之前还是之后，最后一个任务之后是否仍 sleep？",
        [
            "遍历 self.jobs[:] 快照，本轮不会执行新追加的任务",
            "每次 _run_job 后 time.sleep(2)",
            "最后一项之后同样 sleep，没有跳过末项的条件",
        ],
        [("schedule/__init__.py", 103, 120)],
    ),
    (
        "more-itertools/more-itertools",
        "lazy",
        "repeatfunc(function, times, *args) 的 times 为 0、2、None 时分别怎样返回？创建迭代器时会立刻调用 function 吗？请结合两个 starmap 分支解释次数与惰性。",
        [
            "times=0 为空迭代器，times=2 最多产生两次调用",
            "None 使用无限 repeat，需由消费方停止",
            "starmap 返回惰性迭代器，创建时不执行 function",
        ],
        [("more_itertools/recipes.py", 314, 338)],
    ),
    (
        "pallets/flask",
        "defaults",
        "DefaultJSONProvider 的 ensure_ascii 和 sort_keys 属性都为 True 时，调用 dumps(obj, ensure_ascii=False, sort_keys=False) 会被属性值覆盖吗？未传这两个参数时如何处理，调用是否修改 provider 的属性？",
        [
            "显式 False 被保留，setdefault 不覆盖已存在参数",
            "未传时才使用 self.ensure_ascii/self.sort_keys",
            "只修改调用局部 kwargs，不修改 provider 属性",
        ],
        [("src/flask/json/provider.py", 166, 179)],
    ),
    (
        "psf/requests",
        "duplicates",
        "parse_dict_header('a=1, a=2, flag') 对重复键 a 和没有等号的 flag 分别如何处理？沿字典写入与 continue 分支解释，不要把它当作保留重复项的列表。",
        [
            "a 最终为字符串 '2'，后写覆盖前写",
            "flag 对应 None",
            "没有等号时先赋 None 再 continue，其他项按第一个等号拆分后写入 result",
        ],
        [("src/requests/utils.py", 419, 428)],
    ),
]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def freeze(path, value):
    if path.exists():
        if load(path) != value:
            raise ValueError(f"frozen content changed: {path}")
    else:
        write_json(path, value)


def implementation():
    names = [Path(__file__), ROOT / "src/repomentor/interview_baselines.py"]
    return {
        "rag": rag_version(),
        "files": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in names
        },
    }


def profile():
    value = load_chat_settings(ROOT / "config/model-settings.json")
    if (
        value.backend != "ollama"
        or "9b-" not in value.model
        or (value.context_window, value.max_tokens) != (16384, 2048)
    ):
        raise ValueError("requires the pinned local 9B / 16K / 2048 profile")
    return value


def historical_count():
    path = runtime_dir() / "p63/dispatches.sqlite"
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]


def reader_for(task):
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    if reader.snapshot.commit_sha != task["commit_sha"]:
        raise ValueError("snapshot identity differs")
    return reader


def inputs_for(task, condition):
    reader = reader_for(task)
    index = build_index(reader, storage=runtime_dir(), chunker="syntax", retriever="bm25", device="cpu")
    selection = select_sources(reader, index, task["input"]["question"], condition)
    prepared = prepare_input(profile(), task["input"]["question"], selection, condition)
    return reader, selection, prepared


def prepare(phase):
    settings = profile()
    if phase == "development":
        originals = load(ROOT / "experiments/p63/development.json")
        refs = load(ROOT / "experiments/p63/evaluation-only/development.json")
        ids = ["history-dev-04", "history-dev-01", "history-dev-05", "sweqa-flask-002", "sweqa-requests-001"]
        tasks = [next(t for t in originals if t["task_id"] == name) for name in ids]
        extra = next(
            t
            for t in load(ROOT / "experiments/qwen9b-reliability/confirmation.json")
            if t["task_id"] == "reliability-confirm-07"
        )
        tasks.append({**extra, "task_id": "known-first-true"})
        refs["known-first-true"] = {
            "sources": [{"path": "more_itertools/recipes.py", "start_line": 571, "end_line": 588}]
        }
        points = [
            ["不转小写，原始事件键区分大小写", "说明 FOO/foo 或 Bar/bar 的具体测试断言"],
            ["Node 默认入口负数长度抛 RangeError", "核对实际负数输入与异常测试，不能混入 non-secure"],
            ["无 tag 时返回 self.jobs[:] 副本", "列表对象不同，但元素 Job 对象仍共享"],
            [
                "_got_registered_once 阻止注册后的 setup 操作",
                "区别于 first_registration，不能混为同一状态",
                "说明何时设置以及对应异常路径",
            ],
            ["显式 no_proxy 优先于环境变量", "返回非空字典表示未绕过代理并返回环境代理，非绕过成功"],
            ["默认谓词返回 2", "显式 x == 0 返回 0，不会再次按真值丢弃", "filter 筛原始元素，next 取首项"],
        ]
        references = []
        for task, key_points in zip(tasks, points, strict=True):
            ranges = refs[task["task_id"]]["sources"]
            if task["task_id"] == "history-dev-04":
                ranges = [*ranges, {"path": "test/index_test.ts", "start_line": 79, "end_line": 90}]
            references.append({"task_id": task["task_id"], "ranges": ranges, "key_points": key_points})
        for task in tasks:
            task.pop("gold", None)
            task.update(split="reused-development", training_eligible=False)
        freeze(
            OUT / "protocol.json",
            {
                "version": "rag-interview-v1",
                "date": "2026-10-20",
                "settings": settings.public(),
                "implementation": implementation(),
                "conditions": list(CONDITIONS),
                "max_new_requests": CAP,
                "phase_caps": {"development": 30, "confirmation": 30},
                "historical_reserved_at_freeze": historical_count(),
                "combined_limit": 600,
                "same_repositories_only": True,
                "automatic_retry": False,
                "selection": "All five fixed single-call paths specified before generation; no post-development tuning.",
                "ablation": "lexical removes the bundled exact-symbol/static-callee candidate expansion, keeping chunks and windows",
                "confirm_question_commitment": fingerprint(NEW),
                "training_eligible": False,
                "review": "anonymous Codex self-review, not independent human review; simulated users only",
                "performance": "snapshot/index/source assembly through answer completion; prebuilt disk indexes; metadata preflight excluded; uncontrolled model/prefix caches",
            },
        )
    else:
        check()
        if len(list((OUT / "development/cases").glob("*.json"))) != 30:
            raise ValueError("finish the complete development comparison before confirmation")
        bases = load(ROOT / "experiments/qwen9b-reliability/confirmation.json")
        tasks, references = [], []
        for i, (repo, kind, question, points, ranges) in enumerate(NEW, 1):
            base = next(t for t in bases if t["repository"] == repo)
            task = {k: base[k] for k in ("repository", "snapshot_id", "commit_sha")}
            task.update(
                task_id=f"interview-confirm-{i:02}",
                kind=kind,
                input={"question": question},
                split="new-same-repository-confirmation",
                training_eligible=False,
            )
            tasks.append(task)
            references.append(
                {
                    "task_id": task["task_id"],
                    "key_points": points,
                    "ranges": [{"path": p, "start_line": a, "end_line": b} for p, a, b in ranges],
                }
            )
    for task, ref in zip(tasks, references, strict=True):
        reader = reader_for(task)
        ref["sources"] = [
            source_span(
                r["path"], reader.read(r["path"], 500_000)[0].splitlines(), r["start_line"], r["end_line"]
            )
            for r in ref.pop("ranges")
        ]
        verify_spans(reader, ref["sources"])
        ref.update(
            reviewer="Codex source-read before generation", human_approved=False, training_eligible=False
        )
    frozen_inputs = {}
    for task in tasks:
        for condition in CONDITIONS:
            _, selection, prepared = inputs_for(task, condition)
            frozen_inputs[task["task_id"] + "--" + condition] = {"selection": selection, "prepared": prepared}
    freeze(OUT / phase / "tasks.json", tasks)
    freeze(OUT / phase / "evaluation-only.json", references)
    freeze(OUT / phase / "inputs.json", frozen_inputs)
    freeze(
        OUT / phase / "manifest.json",
        {
            "implementation": implementation(),
            "files": {
                name: fingerprint(load(OUT / phase / name))
                for name in ("tasks.json", "evaluation-only.json", "inputs.json")
            },
        },
    )
    print(json.dumps({"prepared": phase, "tasks": len(tasks), "maximum_generation_requests": 30}), flush=True)


def check(phase=None):
    protocol = load(OUT / "protocol.json")
    if protocol["implementation"] != implementation() or protocol["settings"] != profile().public():
        raise ValueError(
            "frozen implementation/model changed; preserve this experiment and use a new identity"
        )
    if protocol["confirm_question_commitment"] != fingerprint(NEW):
        raise ValueError("confirmation questions changed")
    if phase:
        for name, digest in load(OUT / phase / "manifest.json")["files"].items():
            if fingerprint(load(OUT / phase / name)) != digest:
                raise ValueError("frozen phase input changed")
    return protocol


def book():
    return RequestLedger(
        PRIVATE / "dispatches.sqlite",
        limit=CAP,
        experiment=fingerprint(load(OUT / "protocol.json")),
        context_windows=(16384,),
    )


async def run(phase, replay=False):
    protocol = check(phase)
    frozen_inputs = load(OUT / phase / "inputs.json")
    tasks = load(OUT / phase / "tasks.json")
    metadata = None if replay else await ChatCompletionsRuntime(profile()).verify_backend()
    receipts = []
    for ordinal, task in enumerate(tasks):
        order = list(CONDITIONS)
        random.Random(20261020 + ordinal).shuffle(order)
        for condition in order:
            name = task["task_id"] + "--" + condition
            path = OUT / phase / "cases" / (name + ".json")
            if path.exists() and not replay:
                continue
            started = time.perf_counter()
            reader, selection, prepared = inputs_for(task, condition)
            if {"selection": selection, "prepared": prepared} != frozen_inputs[name]:
                raise ValueError("selected input differs from frozen sources")
            retrieval_seconds = time.perf_counter() - started
            if prepared["feasible"]:

                def before(body, budget):
                    if replay:
                        raise AssertionError("replay cannot dispatch")
                    ledger = book()
                    if any(r["case"] == phase + "/" + name for r in ledger.rows()):
                        raise ValueError(
                            "dispatch already reserved; preserve ambiguous failure, never retry automatically"
                        )
                    if historical_count() + len(ledger.rows()) >= protocol["combined_limit"]:
                        raise ValueError("combined historical/new generation cap reached")
                    if sum(r["case"].startswith(phase + "/") for r in ledger.rows()) >= 30:
                        raise ValueError("phase cap reached")
                    ledger.reserve(profile(), phase + "/" + name, body, budget)

                runtime = ChatCompletionsRuntime(profile(), before_dispatch=before)
                index = FrozenSourcesIndex(fingerprint([implementation(), phase, name, selection]), selection)
                engine = RagEngine(reader, index, runtime, PRIVATE / "cache")
                engine.backend = metadata
                result = await engine.answer(task["input"]["question"])
                if result["input_messages"] != prepared["input_messages"]:
                    raise ValueError("actual model input differs from preflight")
            else:
                result = {
                    "answer": None,
                    "context": prepared["context"],
                    "input_messages": prepared["input_messages"],
                    "completion": {
                        "status": "context_limit",
                        "error_code": prepared["reason"],
                        "elapsed_seconds": 0,
                        "request_sent": False,
                        "usage": {},
                    },
                    "new_model_requests": 0,
                    "cache_hit": False,
                    "citation_ranges_valid": None,
                }
            elapsed = time.perf_counter() - started
            if replay:
                old = load(path)["result"]
                if result["answer"] != old["answer"] or result["new_model_requests"]:
                    raise ValueError("replay differs")
                receipts.append({"case": phase + "/" + name, "new_model_requests": 0})
            else:
                row = {
                    "task": task,
                    "condition": condition,
                    "result": result,
                    "feasible": prepared["feasible"],
                    "selection": selection,
                    "prepared_input_sha256": fingerprint(prepared["input_messages"]),
                    "timing": {
                        "end_to_end_seconds": elapsed,
                        "retrieval_assembly_seconds": retrieval_seconds,
                        "model_seconds": result["completion"]["elapsed_seconds"],
                        "cohort": "fresh" if result["new_model_requests"] else "cache_or_infeasible",
                    },
                    "training_eligible": False,
                }
                freeze(path, row)
                print(
                    json.dumps(
                        {
                            "phase": phase,
                            "case": name,
                            "status": result["completion"]["status"],
                            "new_requests": result["new_model_requests"],
                            "seconds": round(elapsed, 2),
                        }
                    ),
                    flush=True,
                )
    if replay:
        write_json(OUT / phase / "replay.json", {"receipts": receipts, "new_model_requests": 0})
    else:
        book().export(OUT / "dispatches.json")


async def replay_all():
    import httpx
    import httpx2

    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("offline replay forbids HTTP")

    async def refuse_async(*args, **kwargs):
        refuse()

    with (
        patch.object(httpx.Client, "send", refuse),
        patch.object(httpx.AsyncClient, "send", refuse_async),
        patch.object(httpx2.Client, "send", refuse),
        patch.object(httpx2.AsyncClient, "send", refuse_async),
    ):
        for phase in ("development", "confirmation"):
            await run(phase, True)
    if attempts:
        raise ValueError("HTTP attempted during replay")
    write_json(
        OUT / "network-free-replay.json",
        {"http_attempts": 0, "generation_requests": 0, "metadata_requests": 0},
    )


def export_review():
    salt_path = PRIVATE / "review-salt.json"
    if not salt_path.exists():
        write_json(salt_path, secrets.token_hex(32))
    salt = load(salt_path)
    rows, mapping = [], {}
    for phase in ("development", "confirmation"):
        refs = {r["task_id"]: r for r in load(OUT / phase / "evaluation-only.json")}
        for path in sorted((OUT / phase / "cases").glob("*.json")):
            case = load(path)
            candidate_id = fingerprint([salt, phase, path.stem])[:16]
            mapping[candidate_id] = {"source": path.relative_to(ROOT).as_posix(), "sha256": fingerprint(case)}
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "question": case["task"]["input"]["question"],
                    "snapshot_id": case["task"]["snapshot_id"],
                    "answer": case["result"]["answer"],
                    "completion_status": case["result"]["completion"]["status"],
                    "observed_context": case["result"]["context"],
                    "evaluation_sources": refs[case["task"]["task_id"]]["sources"],
                    "required_points": refs[case["task"]["task_id"]]["key_points"],
                }
            )
    rows.sort(key=lambda r: r["candidate_id"])
    freeze(PRIVATE / "review-mapping.json", mapping)
    freeze(OUT / "review/packet.json", rows)
    freeze(
        OUT / "review/rubric.json",
        {
            **RUBRIC,
            "reviewer": "Codex simulated reviewer",
            "blinding": "Condition labels removed; source length/style may reveal condition. Implementer is not independent.",
            "human_approved": False,
            "independent_review_completed": False,
        },
    )
    print(json.dumps({"anonymous_candidates": len(rows), "mapping": "private runtime only"}), flush=True)


def summarize():
    packet = {r["candidate_id"]: r for r in load(OUT / "review/packet.json")}
    mapping = load(PRIVATE / "review-mapping.json")
    decisions_path = OUT / "review/decisions.json"
    decisions = load(decisions_path) if decisions_path.exists() else []
    judgments = {}
    for value in decisions:
        cid = value["candidate_id"]
        if cid in judgments or cid not in packet:
            raise ValueError("duplicate/unknown review candidate")
        case = load(ROOT / mapping[cid]["source"])
        if fingerprint(case) != mapping[cid]["sha256"]:
            raise ValueError("review bound case changed")
        j = validate_judgment(value, packet[cid], reader_for(case["task"]))
        j["solved"] = (
            packet[cid]["completion_status"] == "success"
            and j["correctness"] == "correct"
            and j["citation_support"] in {"supported", "not_applicable"}
            and j["completeness"] == "adequate"
            and j["abstention"] != "inappropriate"
            and j["consistency"] == "consistent"
        )
        judgments[cid] = j
    groups = {}
    for phase in ("development", "confirmation"):
        refs = {r["task_id"]: r for r in load(OUT / phase / "evaluation-only.json")}
        for condition in CONDITIONS:
            rows = [load(p) for p in (OUT / phase / "cases").glob("*--" + condition + ".json")]
            cohort = [r["timing"]["end_to_end_seconds"] for r in rows if r["timing"]["cohort"] == "fresh"]
            reviews = []
            for cid, meta in mapping.items():
                if meta["source"].startswith("experiments/rag-interview/" + phase + "/") and meta[
                    "source"
                ].endswith("--" + condition + ".json"):
                    if cid in judgments:
                        reviews.append(judgments[cid])
            groups[phase + "/" + condition] = {
                "tasks": len(rows),
                "statuses": dict(Counter(r["result"]["completion"]["status"] for r in rows)),
                "reviewed": len(reviews),
                "solved": sum(j["solved"] for j in reviews) if len(reviews) == len(rows) else None,
                "correct": sum(j["correctness"] == "correct" for j in reviews),
                "adequate": sum(j["completeness"] == "adequate" for j in reviews),
                "supported": sum(j["citation_support"] == "supported" for j in reviews),
                "mean_reference_coverage": statistics.mean(
                    coverage(refs[r["task"]["task_id"]]["sources"], r["result"]["context"]) for r in rows
                )
                if rows
                else None,
                "complete_reference_tasks": sum(
                    coverage(refs[r["task"]["task_id"]]["sources"], r["result"]["context"]) == 1 for r in rows
                ),
                "new_model_requests": sum(r["result"]["new_model_requests"] for r in rows),
                "fresh_latency_samples": len(cohort),
                "end_to_end_p50_seconds": percentile(cohort, 0.5),
                "end_to_end_p95_seconds": percentile(cohort, 0.95),
            }
    write_json(OUT / "judgments.json", judgments)
    write_json(
        OUT / "summary.json",
        {
            "groups": groups,
            "reviewed": len(judgments),
            "expected_reviews": len(packet),
            "new_requests": len(book().rows()),
            "historical_reserved": historical_count(),
            "combined_requests": historical_count() + len(book().rows()),
            "human_approved": False,
            "independent_review_completed": False,
            "real_user_study_completed": False,
            "training_eligible": False,
        },
    )
    print(
        json.dumps(
            {"reviewed": len(judgments), "candidates": len(packet), "new_requests": len(book().rows())}
        ),
        flush=True,
    )


def audit():
    for phase in ("development", "confirmation"):
        check(phase)
        for path in (OUT / phase / "cases").glob("*.json"):
            case = load(path)
            verify_spans(reader_for(case["task"]), case["result"]["context"])
            if fingerprint(case["result"]["input_messages"]) != case["prepared_input_sha256"]:
                raise ValueError("actual input changed")
            payload = json.loads(case["result"]["input_messages"][-1]["content"])
            if set(payload) != {"question", "context"}:
                raise ValueError("evaluation/gold fields reached the model")
    if len(book().rows()) > CAP or historical_count() + len(book().rows()) > 600:
        raise ValueError("request cap exceeded")
    write_json(
        OUT / "audit.json",
        {
            "source_hashes_valid": True,
            "gold_not_in_input": True,
            "new_requests": len(book().rows()),
            "combined_requests": historical_count() + len(book().rows()),
            "paid_requests": 0,
            "training_eligible": False,
        },
    )


def finalize():
    audit()
    summarize()
    hashes = {
        p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(OUT.rglob("*"))
        if p.is_file() and p.name != "completed-files.json"
    }
    freeze(OUT / "completed-files.json", {"sha256": hashes, "training_eligible": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("prepare", "run", "replay", "export-review", "summarize", "audit", "finalize")
    )
    parser.add_argument("--phase", choices=("development", "confirmation"), default="development")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.phase)
    elif args.action == "run":
        asyncio.run(run(args.phase))
    elif args.action == "replay":
        asyncio.run(replay_all())
    elif args.action == "export-review":
        export_review()
    else:
        {"summarize": summarize, "audit": audit, "finalize": finalize}[args.action]()


if __name__ == "__main__":
    main()
