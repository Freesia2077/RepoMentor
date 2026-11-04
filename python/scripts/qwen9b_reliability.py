"""Bounded 9B reliability validation: prepare, run, replay, review, summarize, audit."""

import argparse
import asyncio
import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from repomentor.agent_tools import AgentLimits
from repomentor.config import load_chat_settings, write_json
from repomentor.conversation import ConversationStore
from repomentor.entry_scope import resolve_entry
from repomentor.experiment_ledger import RequestLedger
from repomentor.paths import data_root, runtime_dir
from repomentor.presentation import render_run
from repomentor.rag import RagEngine, rag_version, verify_spans
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version
from repomentor.sdk_report import SdkReport
from repomentor.semantic_review import validate_judgment
from repomentor.source_windows import source_span

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/qwen9b-reliability"
PRIVATE = runtime_dir() / "qwen9b-reliability"
PREFIX = "qwen9b-reliability"
CAPS = {"development": 18, "confirmation": 84, "dialogue": 24, "reports": 18}
LIMITS = AgentLimits(max_decisions=6, max_tool_calls=6, timeout_seconds=360)
# New questions in the same six development repositories; not unseen-repository generalization.
CONFIRMATION = [
    ("developit/mitt", "cross_function", "mitt 的普通事件 handler 在执行时再 on 同类型的新 handler，新 handler 会在这一次 emit 中执行吗？结合注册与派发的实现说明。",
     "本轮不会；on 修改原 handlers 数组，而 emit 在调用前使用 slice 快照，后续 emit 才会看到新增项。",
     [("src/index.ts", 66, 73), ("src/index.ts", 103, 110)]),
    ("developit/mitt", "tests", "mitt.on 支持 Symbol 事件名吗？实现是否转成字符串，仓库的对应测试如何验证保留 Symbol 键？",
     "支持；on 直接以 type 为 Map 键，没有字符串转换；符号测试用同一个 eventType 查询 Map 并检查注册 handler。",
     [("src/index.ts", 66, 73), ("test/index_test.ts", 26, 26), ("test/index_test.ts", 92, 96)]),
    ("ai/nanoid", "behavior", "Node 的 customRandom 在 alphabet 长度为 2 和 3 时如何把随机字节映射为字符？随机字节 255 在两条分支中会怎样处理？",
     "长度2走 safeByteCutoff=256 的 mask=1 分支，255 映射到索引1；长度3 cutoff=255，255被拒绝，合格字节取模3。",
     [("index.js", 29, 59), ("index.js", 68, 86)]),
    ("ai/nanoid", "behavior", "Node customAlphabet('ab', 4) 返回的生成器，分别不传长度、传 2、传 0 时返回多长？长度 2 会被 defaultSize 强行改为 4 吗？沿参数与截取路径说明。",
     "长度依次4、2、0；闭包默认参数仅在未传时采用defaultSize，实际size控制poolOffset和substring，0提前返回空串。",
     [("index.js", 96, 110), ("index.js", 133, 173)]),
    ("dbader/schedule", "cross_function", "Scheduler.jobs 非空但 get_next_run(tag) 找不到匹配标签时，会返回所有任务中最早的时间吗？如何复用 get_jobs，匹配成功后怎样选择？",
     "不会回退；get_jobs(tag)过滤，结果为空返回None；匹配成功返回min(jobs_filtered).next_run。",
     [("schedule/__init__.py", 122, 133), ("schedule/__init__.py", 177, 195)]),
    ("dbader/schedule", "cross_function", "Scheduler.every(2) 本身会把 Job 加入 jobs 吗？正常设置时间单位后，Job.do 怎样保存回调及参数、加入列表，又返回什么？",
     "every仅构造并返回Job；do用functools.partial绑定函数和参数，更新包装信息并计算next_run，追加自身到scheduler.jobs，返回自身。",
     [("schedule/__init__.py", 162, 170), ("schedule/__init__.py", 644, 664)]),
    ("more-itertools/more-itertools", "behavior", "first_true([0, False, 2], default='missing') 返回什么？如果 pred=lambda x: x == 0，返回的 0 会因为是假值而被丢弃吗？结合实现解释。",
     "默认pred=None按真值筛选，返回2；显式谓词匹配首项0，filter根据谓词结果保留原项，next返回0，不再次判断其真值。",
     [("more_itertools/recipes.py", 571, 588)]),
    ("more-itertools/more-itertools", "behavior", "all_equal([], key=str.casefold) 与 all_equal('AaaA', key=str.casefold) 分别返回什么？实现会先排序所有元素再比较吗？",
     "均True；使用groupby(iterable,key)，发现第二组才False，不排序；空输入直接走最后return True。",
     [("more_itertools/recipes.py", 219, 242)]),
    ("pallets/flask", "cross_function", "DefaultJSONProvider.default 怎样处理 Decimal、UUID 和普通不可序列化对象？为什么不能把未知对象自动说成 JSON null？沿 default 与 _default 的关联说明。",
     "default是staticmethod(_default)；Decimal和UUID转str，未命中任何支持分支的普通对象抛TypeError，不返回None/null。",
     [("src/flask/json/provider.py", 108, 141)]),
    ("pallets/flask", "cross_function", "Blueprint.record_once(func) 在允许设置的阶段调用时会立即执行 func 吗？record 与 wrapper 各负责什么，first_registration 为 False 或 True 时分别怎样处理？",
     "不立即执行func；record将包装器登记到deferred_functions；包装器在state.first_registration为真时调用func(state)，为假跳过。",
     [("src/flask/sansio/blueprints.py", 223, 244)]),
    ("psf/requests", "behavior", "select_proxy 对 https://example.com/a 在同时配置 https://example.com、https、all://example.com、all 时按什么顺序选择？第一个键存在但值为 None 时会继续回退吗？",
     "按scheme+host、scheme、all+host、all顺序；判断键存在而不是值真伪，第一个键值None也立即break返回None。",
     [("src/requests/utils.py", 828, 851)]),
    ("psf/requests", "cross_function", "resolve_proxies(..., trust_env=False) 是否仍查询环境代理，返回原 proxies 字典还是副本？trust_env=True 时环境代理会覆盖已有 scheme 键吗？",
     "False使短路条件跳过环境查询；返回proxies.copy副本；True且未绕过时取环境scheme/all并setdefault补全，已有scheme键不被覆盖。",
     [("src/requests/utils.py", 854, 878)]),
]
DIALOGUE = [
    "mitt 的 on 会把事件名转成小写吗？请结合测试的具体输入和断言说明。",
    "那么 off('FOO') 不传 handler，是删除 Map 的键还是清空对应数组？",
    "切换到 emit，普通 handler 和通配符 handler 的参数、调用顺序分别是什么？",
    "这能证明该仓库的测试今天已经在我的电脑上执行通过了吗？如果不能，还缺什么？",
]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def freeze(path, value):
    if Path(path).exists():
        if load(path) != value:
            raise ValueError(f"frozen identity changed: {path}")
    else:
        write_json(Path(path), value)


def profile():
    value = load_chat_settings(ROOT / "config/model-settings.json")
    if value.backend != "ollama" or "9b-" not in value.model or (value.context_window, value.max_tokens) != (16384, 2048):
        raise ValueError("expected the authorized 9B / 16K / 2048 local profile")
    return value


def open_task(task):
    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    assert reader.snapshot.commit_sha == task["commit_sha"]
    return reader


def index_for(reader):
    return build_index(reader, storage=runtime_dir(), chunker="syntax", retriever="bm25", device="cpu")


def book(phase):
    return RequestLedger(runtime_dir() / "p63/dispatches.sqlite", limit=600,
        experiment=fingerprint(load(ROOT / "experiments/p63/protocol.json")), context_windows=(16384,),
        batch_prefix=PREFIX + "/" + phase, batch_limit=CAPS[phase])


def prepare():
    known = load(ROOT / "experiments/p63/confirmation.json")
    tasks, refs = [], []
    for i, (repo, kind, question, expected, ranges) in enumerate(CONFIRMATION, 1):
        base = next(t for t in known if t["repository"] == repo)
        task = {k: base[k] for k in ("repository", "snapshot_id", "commit_sha")}
        task.update(task_id=f"reliability-confirm-{i:02}", kind=kind, input={"question": question},
                    split="same-repository-confirmation", training_eligible=False)
        reader = open_task(task)
        sources = []
        for path, first, last in ranges:
            lines = reader.read(path, 500_000)[0].splitlines()
            if not 1 <= first <= last <= len(lines):
                raise ValueError("invalid reviewed reference")
            sources.append(source_span(path, lines, first, last))
        verify_spans(reader, sources)
        refs.append({"task_id": task["task_id"], "expected": expected, "references": sources,
                     "reviewer": "Codex source-reviewed before generation", "human_approved": False,
                     "training_eligible": False})
        tasks.append(task)
    dev_ids = ["history-dev-04", "sweqa-flask-002", "sweqa-requests-001"]
    development = [next(t for t in load(ROOT / "experiments/p63/development.json") if t["task_id"] == name)
                   for name in dev_ids]
    initial, index_ids = {}, {}
    for task in [*development, *tasks]:
        reader = open_task(task)
        index = index_for(reader)
        question = task["input"]["question"]
        focus = resolve_entry(reader, question)
        initial[task["task_id"]] = index.initial_sources(question, excluded=focus["excluded_paths"])
        index_ids[task["task_id"]] = index.index_id
    for name, value in [("development.json", development), ("confirmation.json", tasks),
                        ("evaluation-only/confirmation.json", refs), ("initial-retrieval.json", initial),
                        ("dialogue.json", {"repository": "developit/mitt", "questions": DIALOGUE,
                                           "training_eligible": False})]:
        freeze(OUT / name, value)
    freeze(OUT / "protocol.json", {"version": "qwen9b-reliability-v1", "date": "2026-10-20",
        "profile": profile().public(), "config_id": profile().config_id, "sdk": sdk_version(), "rag": rag_version(),
        "limits": LIMITS.model_dump(), "research_output_tokens": 1536, "final_output_tokens": 2048,
        "retrieval": {"chunker": "syntax", "retriever": "bm25", "device": "cpu", "index_ids": index_ids},
        "caps": CAPS, "maximum_new_requests": sum(CAPS.values()), "global_cap": 600,
        "previous_requests": 447, "paid_requests": 0, "automatic_retry": False,
        "sdk_sources": ["https://developers.openai.com/api/docs/guides/agents/results",
                        "https://developers.openai.com/api/docs/guides/agents/running-agents"],
        "inputs": {name: fingerprint(load(OUT / name)) for name in ["development.json", "confirmation.json",
                    "evaluation-only/confirmation.json", "initial-retrieval.json", "dialogue.json"]},
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "conditions": {"fixed": "one completion on frozen initial retrieval", "sdk": "same initial retrieval plus SDK tools"},
        "interpretation": "Bundled source-reading, notes and budget change; not component causal attribution. "
                          "New same-repository questions, nonblind Codex review; never training data.",
        "human_approved": False, "training_eligible": False})
    print(json.dumps({"prepared": True, "new_request_cap": sum(CAPS.values())}), flush=True)


def check_identity():
    identity = load(OUT / "protocol.json")
    assert identity["profile"] == profile().public() and identity["sdk"] == sdk_version() and identity["rag"] == rag_version()
    assert identity["runner_sha256"] == hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    for name, expected in identity["inputs"].items():
        assert fingerprint(load(OUT / name)) == expected, name


class FrozenInitialIndex:
    def __init__(self, base, initial):
        self.base, self.initial = base, initial

    def __getattr__(self, name):
        return getattr(self.base, name)

    def initial_sources(self, question, *, excluded=()):
        assert not any(s["path"] in excluded for s in self.initial["sources"])
        return copy.deepcopy(self.initial)


def runtime(phase, name, mode, replay):
    settings = profile()

    def before(body, budget):
        assert not replay, "replay cannot dispatch generation"
        book(phase).reserve(settings, PREFIX + "/" + phase + "/" + name, body, budget)

    class Fixed(ChatCompletionsRuntime):
        async def verify_backend(self):
            assert not replay, "replay cannot query model metadata"
            return await super().verify_backend()

    class Sdk(OpenAISdkRuntime):
        async def verify_backend(self):
            assert not replay, "replay cannot query model metadata"
            return await super().verify_backend()

    return (Fixed if mode == "fixed" else Sdk)(settings, before_dispatch=before)


def save_case(phase, name, case):
    freeze(OUT / phase / "cases" / (name + ".json"), {**case, "training_eligible": False})
    target = OUT / "markdown" / (phase + "--" + name + ".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_run(case), encoding="utf-8")
    record = case.get("result")
    print(json.dumps({"phase": phase, "case": name, "status": record["completion"]["status"] if record else case["report"]["status"],
        "requests": record["new_model_requests"] if record else sum(r["new_model_requests"] for r in case["stages"])}), flush=True)


async def qa(phase, replay):
    initial = load(OUT / "initial-retrieval.json")
    receipts = []
    for task in load(OUT / (phase + ".json")):
        for mode in (["sdk"] if phase == "development" else ["fixed", "sdk"]):
            name = task["task_id"] + "--" + mode
            path = OUT / phase / "cases" / (name + ".json")
            if path.exists() and not replay:
                print(json.dumps({"saved": phase + "/" + name, "generation_requests": 0}), flush=True)
                continue
            if replay and not path.exists():
                raise ValueError("cannot replay an ungenerated task")
            reader = open_task(task)
            index = FrozenInitialIndex(index_for(reader), initial[task["task_id"]])
            rt = runtime(phase, name, mode, replay)
            engine = (RagEngine(reader, index, rt, PRIVATE / "fixed-cache") if mode == "fixed" else
                      SdkAgentEngine(reader, index, rt, PRIVATE / "sdk-cache", LIMITS))
            result = await engine.answer(task["input"]["question"])
            assert result["initial_retrieval"] == initial[task["task_id"]]
            if replay:
                old = load(path)["result"]
                assert result["attempt_id"] == old["attempt_id"] and result["new_model_requests"] == 0
                receipts.append({"case": phase + "/" + name, "attempt_id": old["attempt_id"]})
            else:
                save_case(phase, name, {**task, "mode": mode, "result": result})
    return receipts


async def dialogue(replay):
    task = next(t for t in load(OUT / "confirmation.json") if t["repository"] == "developit/mitt")
    reader = open_task(task)
    store = ConversationStore(PRIVATE / "conversations")
    state_path = PRIVATE / "dialogue-session.json"
    if state_path.exists():
        state = load(state_path)
    else:
        assert not replay
        state = store.create(task["snapshot_id"], "核对 mitt 实现、测试与不可验证信息")
        write_json(state_path, state)
    engine = SdkAgentEngine(reader, index_for(reader), runtime("dialogue", "mitt", "sdk", replay),
                            PRIVATE / "sdk-cache", LIMITS)
    receipts = []
    for i, question in enumerate(DIALOGUE, 1):
        name = f"mitt-turn-{i}"
        path = OUT / "dialogue/cases" / (name + ".json")
        turn = await store.turn(state["session_id"], engine, question, request_id=name)
        result = turn["result"]
        if replay:
            assert result["attempt_id"] == load(path)["result"]["attempt_id"]
            assert turn.get("replayed") and turn.get("new_model_requests", 0) == 0
            receipts.append({"case": "dialogue/" + name, "attempt_id": result["attempt_id"]})
        elif not path.exists():
            save_case("dialogue", name, {**task, "task_id": name, "mode": "sdk", "question": question,
                "linked_turn": i, "session_id": state["session_id"], "result": result})
        if result["completion"]["status"] != "success":
            write_json(OUT / "dialogue/blocked.json", {"after_turn": i, "remaining_turns": 4-i,
                        "reason": "do not retry a failed linked turn automatically"})
            break
    return receipts


async def reports(replay):
    task = next(t for t in load(OUT / "confirmation.json") if t["repository"] == "developit/mitt")
    path = OUT / "reports/cases/mitt.json"
    if path.exists() and not replay:
        return []
    reader = open_task(task)
    report = SdkReport(reader, index_for(reader), runtime("reports", "mitt", "sdk", replay),
                       PRIVATE / "sdk-cache", limits=LIMITS)
    result = await report.run(total_timeout=900)
    if replay:
        saved = load(path)
        assert result.outputs == saved["report"]["outputs"] and result.status == saved["report"]["status"]
        assert [r["attempt_id"] for r in report.records] == [r["attempt_id"] for r in saved["stages"]]
        assert all(r["new_model_requests"] == 0 for r in report.records)
        return [{"case": "reports/mitt/" + r["stage"], "attempt_id": r["attempt_id"]} for r in report.records]
    save_case("reports", "mitt", {**task, "mode": "sdk", "report": result.model_dump(), "stages": report.records})
    return []


async def run(phase, replay=False):
    check_identity()
    receipts = await (qa(phase, replay) if phase in ("development", "confirmation") else
                      dialogue(replay) if phase == "dialogue" else reports(replay))
    if replay:
        write_json(OUT / (phase + "/replay.json"), {"receipts": receipts, "generation_requests": 0, "metadata_requests": 0})
    book(phase).export(OUT / "dispatches.json")


async def replay_all():
    from unittest.mock import patch

    import httpx
    import httpx2

    attempts = []

    def refuse(*a, **kw):
        attempts.append(True)
        raise AssertionError("offline replay forbids all HTTP")

    async def refuse_async(*a, **kw):
        refuse()

    with (patch.object(httpx.Client, "send", refuse), patch.object(httpx.AsyncClient, "send", refuse_async),
          patch.object(httpx2.Client, "send", refuse), patch.object(httpx2.AsyncClient, "send", refuse_async)):
        for phase in CAPS:
            await run(phase, True)
    assert not attempts
    write_json(OUT / "network-free-replay.json", {"http_send_attempts": 0, "generation_requests": 0, "metadata_requests": 0})


def candidates():
    rows = []
    for phase in CAPS:
        for path in sorted((OUT / phase / "cases").glob("*.json")):
            case = load(path)
            for record in case.get("stages") or [case["result"]]:
                stage = record.get("stage", "answer")
                published = case.get("report", {}).get("outputs", {}).get(stage)
                rows.append({"candidate_id": phase + "/" + path.stem + "/" + stage,
                    "phase": phase, "mode": case["mode"], "stage": stage, "task_id": case["task_id"],
                    "question": record["question"], "snapshot_id": case["snapshot_id"], "commit_sha": case["commit_sha"],
                    "source_file": path.relative_to(ROOT).as_posix(), "record_sha256": fingerprint(record),
                    "completion_status": record["completion"]["status"],
                    "answer": published if published is not None else record["answer"],
                    "published": published is not None if phase == "reports" else record["completion"]["status"] == "success",
                    "observed_context": record.get("final_context", record["context"])})
    return rows


def summarize():
    rows = candidates()
    assert rows == load(OUT / "review-candidates.json")
    decisions = {r["candidate_id"]: r for r in load(OUT / "review-decisions.json")}
    assert set(decisions) == {r["candidate_id"] for r in rows}
    groups, judgments = defaultdict(list), []
    for row in rows:
        judgment = validate_judgment(decisions[row["candidate_id"]], row, open_task(row))
        judgment["solved"] = (row["published"] and row["completion_status"] == "success"
            and judgment["correctness"] == "correct" and judgment["completeness"] == "adequate"
            and judgment["citation_support"] in {"supported", "not_applicable"}
            and judgment["abstention"] != "inappropriate" and judgment["consistency"] == "consistent")
        judgments.append(judgment)
        case = load(ROOT / row["source_file"])
        record = next((r for r in case.get("stages", []) if r["stage"] == row["stage"]), case.get("result"))
        groups[row["phase"] + "/" + row["mode"]].append((row, judgment, record))
    stats = {}
    for name, items in groups.items():
        stats[name] = {"outputs": len(items), "solved": sum(j["solved"] for _, j, _ in items),
            "correctness": dict(Counter(j["correctness"] for _, j, _ in items)),
            "citation_support": dict(Counter(j["citation_support"] for _, j, _ in items)),
            "completeness": dict(Counter(j["completeness"] for _, j, _ in items)),
            "requests": sum(r.get("total_model_requests", r["new_model_requests"]) for _, _, r in items),
            "tool_cases": sum(bool(r.get("tool_calls")) for _, _, r in items),
            "seconds": round(sum(r.get("elapsed_seconds", r["completion"]["elapsed_seconds"]) for _, _, r in items), 3)}
    paired = []
    for task in load(OUT / "confirmation.json"):
        pair = {}
        for mode in ("fixed", "sdk"):
            pair[mode] = next(j["solved"] for row, j, _ in groups["confirmation/" + mode] if row["task_id"] == task["task_id"])
        paired.append({"task_id": task["task_id"], "kind": task["kind"], **pair})
    write_json(OUT / "judgments.json", judgments)
    write_json(OUT / "summary.json", {"groups": stats, "paired": paired,
        "sdk_gains": sum(p["sdk"] and not p["fixed"] for p in paired),
        "sdk_regressions": sum(p["fixed"] and not p["sdk"] for p in paired),
        "reviewer": "Codex nonblind model review", "human_approved": False, "training_eligible": False})


def audit():
    check_identity()
    for phase in CAPS:
        for path in (OUT / phase / "cases").glob("*.json"):
            case = load(path)
            reader = open_task(case)
            for record in case.get("stages") or [case["result"]]:
                verify_spans(reader, record["context"])
                verify_spans(reader, record.get("final_context", []))
                for call in record.get("decisions", [record["completion"]]):
                    if call.get("request_sent"):
                        b = call["input_budget"]
                        assert b["fits"] and b["total"] <= b["context_window"] == 16384
    rows = book("development").rows()
    count = sum(r["case"].startswith(PREFIX + "/") for r in rows)
    assert count <= sum(CAPS.values()) and len(rows) <= 600
    write_json(OUT / "audit.json", {"recorded_outputs": len(candidates()), "reserved_requests": count,
        "global_reserved_requests": len(rows), "new_cap": sum(CAPS.values()), "paid_requests": 0,
        "source_and_budget_checks_passed": True, "semantic_correctness_inferred": False})
    print(json.dumps(load(OUT / "audit.json")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run", "replay", "export-review", "summarize", "audit"])
    parser.add_argument("--phase", choices=list(CAPS), default="development")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "run":
        asyncio.run(run(args.phase))
    elif args.action == "replay":
        asyncio.run(replay_all())
    elif args.action == "export-review":
        write_json(OUT / "review-candidates.json", candidates())
    else:
        {"summarize": summarize, "audit": audit}[args.action]()


if __name__ == "__main__":
    main()
