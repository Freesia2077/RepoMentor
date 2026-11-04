"""Frozen local SDK suite: 16 QA, four three-turn conversations, two full reports."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from p6_archive import read_jsonl, sha, verify_archive

from repomentor.agent_tools import AgentLimits
from repomentor.config import load_chat_settings
from repomentor.conversation import ConversationStore
from repomentor.harness import HarnessError
from repomentor.paths import data_root, runtime_dir
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import fingerprint
from repomentor.runtimes.openai_sdk import OpenAISdkRuntime
from repomentor.sdk_agent import SdkAgentEngine, sdk_version
from repomentor.sdk_report import SdkReport
from repomentor.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-sdk-e2e"
SELECTION = "full"
TASK_IDS = [
    "developit--mitt-p4-cross_map", "developit--mitt-p4-cross_case", "developit--mitt-06",
    "developit--mitt-p4-unknown_runtime", "ai--nanoid-p4-cross_custom", "ai--nanoid-p4-cross_negative",
    "ai--nanoid-12", "ai--nanoid-p4-unknown_business", "dbader--schedule-01", "dbader--schedule-03",
    "dbader--schedule-04", "dbader--schedule-08", "more-itertools--more-itertools-01",
    "more-itertools--more-itertools-03", "more-itertools--more-itertools-14", "more-itertools--more-itertools-15",
]
CONVERSATIONS = [
    ("mitt", "developit--mitt-p4-cross_case", [
        ("那么它的 off 传入未注册的 handler 会抛错或误删最后一个吗？", "developit--mitt-06"),
        ("如果这个 off 不传 handler，是删除 Map 的键还是把对应列表清空？", "developit--mitt-05"),
    ]),
    ("nanoid", "ai--nanoid-p4-cross_custom", [
        ("那么它返回的函数可以在每次调用时再次指定长度吗？请核对测试的实际调用。", "ai--nanoid-p4-cross_custom"),
        ("切换到 Node 版 nanoid：它收到负数长度会怎样？不要混入其他入口的行为。", "ai--nanoid-p4-cross_negative"),
    ]),
    ("schedule", "dbader--schedule-01", [
        ("那么 run_all 与这个 run_pending 在执行条件上有什么区别？", "dbader--schedule-03"),
        ("这个 Scheduler 的 cancel_job 删除未注册任务会抛 ValueError 吗？", "dbader--schedule-06"),
    ]),
    ("itertools", "more-itertools--more-itertools-01", [
        ("那么它在输入不足 n 项时会补齐还是只返回已有元素？", "more-itertools--more-itertools-01"),
        ("换到 tail：它的 n 为负数时会怎样？", "more-itertools--more-itertools-03"),
    ]),
]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def settings():
    profile = load_chat_settings(ROOT / "config/model-settings.qwen-local.json")
    endpoint = urlparse(profile.baseUrl)
    if (profile.backend != "ollama" or endpoint.hostname != "127.0.0.1" or endpoint.port != 11435
            or profile.context_window != 8192):
        raise ValueError("suite requires the existing loopback Ollama service and pinned 8K profile")
    return profile


def prepare():
    tasks = {t["task_id"]: t for t in read_jsonl(ROOT / "experiments/p4/tasks.jsonl")}
    cases, review = [], {}

    def case(key, kind, task_id, question=None, **extra):
        task = tasks[task_id]
        assert task["split"] == "validation"
        value = {"case_id": key, "kind": kind, "task_id": task_id, "split": "validation",
                 "snapshot_id": task["snapshot_id"], "repository": task["repository"],
                 "commit_sha": task["commit_sha"], "question": question or task["input"]["question"], **extra}
        cases.append(value)
        review[key] = {"gold": task["gold"], "human_approved": False}

    for task_id in TASK_IDS:
        case("qa-" + task_id, "qa", task_id)
    for name, first, followups in CONVERSATIONS:
        case("chat-" + name + "-1", "chat", first, group=name, turn=1)
        for turn, (question, task_id) in enumerate(followups, 2):
            case(f"chat-{name}-{turn}", "chat", task_id, question, group=name, turn=turn)
    for name, task_id in (("mitt", "developit--mitt-p4-cross_case"), ("schedule", "dbader--schedule-01")):
        case("report-" + name, "report", task_id)
        review["report-" + name] = {"human_approved": False, "rubric": [
            "Each role grounds repository claims in its own observed sources.",
            "Project purpose, entry points, architecture and reading path are usable.",
            "Setup/test commands need observed support; suggestions are not real issues.",
        ]}
    profile = settings()
    if SELECTION == "budget-regression":
        cases = [c for c in cases if c["kind"] == "report" or c["case_id"] in (
            "qa-developit--mitt-p4-cross_case", "qa-ai--nanoid-p4-unknown_business")]
        review = {c["case_id"]: review[c["case_id"]] for c in cases}
    protected = {p.relative_to(ROOT).as_posix(): sha(p)
                 for folder in ("p6", "p6-sdk") for p in sorted((ROOT / "experiments" / folder).rglob("*.json*"))}
    value = {
        "schema_version": "repomentor.sdk.e2e.protocol.v1", "split": "validation", "human_approved": False,
        "sdk": sdk_version(), "qa_config": OpenAISdkRuntime(profile).settings.public(),
        "report_config": OpenAISdkRuntime(profile.model_copy(update={"max_tokens": 2048})).settings.public(),
        "qa_limits": AgentLimits(max_tool_calls=5, max_decisions=6, timeout_seconds=240).model_dump(),
        "report_limits": AgentLimits(max_tool_calls=4, max_decisions=5, timeout_seconds=240).model_dump(),
        "retrieval": {"chunker": "syntax", "retriever": "bm25", "device": "cpu", "query_expansion": True},
        "max_local_requests": 180 if SELECTION == "full" else 42, "automatic_retry": False, "cases": cases,
        "protected_files": protected, "tasks_sha256": sha(ROOT / "experiments/p4/tasks.jsonl"),
        "grading": "Protocol/schema success is separate from source-grounded semantic review. Development set only.",
        "cache_policy": "Reuse identical initial QA for chat turn 1; persist failures without automatic reruns.",
    }
    for filename, expected in (("protocol.json", value), ("review-reference.json", review)):
        path = OUT / filename
        if path.exists():
            if load(path) != expected:
                raise ValueError(f"frozen {filename} changed; preserve the old suite and use a new version")
        else:
            write(path, expected)
    return value


class SuiteRuntime(OpenAISdkRuntime):
    """One admission counter around the SDK HTTP hook; no agent iteration here."""

    def __init__(self, profile, protocol, replay):
        super().__init__(profile)
        self.protocol, self.replay = protocol, replay

    async def verify_backend(self):
        if self.replay:
            raise AssertionError("replay forbids metadata and inference requests")
        return await super().verify_backend()

    async def _request(self, request):
        if self.replay:
            raise AssertionError("replay cannot dispatch inference")
        ledger = OUT / "dispatches.json"
        entries = load(ledger) if ledger.exists() else []
        if len(entries) >= self.protocol["max_local_requests"]:
            raise HarnessError("suite_request_budget", "local suite request cap reached")
        await super()._request(request)
        entries.append({"ordinal": len(entries) + 1, "request_hash": self.active["request_hash"]})
        write(ledger, entries)  # Ambiguous dispatches count too; never retry implicitly.


async def run(replay=False):
    protocol = prepare()
    verify_archive()
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name
    profile = settings()
    corpus = SnapshotStore(data_root() / "corpus")
    store = ConversationStore(runtime_dir() / "sdk-e2e/conversations")
    receipts = []
    for case in protocol["cases"]:
        path = OUT / "cases" / (case["case_id"] + ".json")
        if path.exists() and not replay:
            print(json.dumps({"case": case["case_id"], "saved": True}), flush=True)
            continue
        reader = corpus.open(case["snapshot_id"])
        assert reader.snapshot.commit_sha == case["commit_sha"]
        index = build_index(reader, storage=runtime_dir(), profile=profile, **protocol["retrieval"])
        is_report = case["kind"] == "report"
        runtime = SuiteRuntime(profile.model_copy(update={"max_tokens": 2048}) if is_report else profile,
                               protocol, replay)
        limits = AgentLimits(**protocol["report_limits" if is_report else "qa_limits"])
        engine = SdkAgentEngine(reader, index, runtime, runtime_dir() / "sdk/cache", limits)
        if is_report:
            report = SdkReport(reader, index, runtime, engine.cache_dir, limits=limits)
            # Separate future experiment versions in the runtime ledger as well as in frozen outputs.
            run_id = "sdk-e2e-" + fingerprint(protocol)[:10] + "-" + case["case_id"]
            ledger = None if replay else RunStore(runtime_dir(), run_id)
            result = await report.run(run_id=run_id, emit=ledger.emit if ledger else lambda *args: None,
                                      total_timeout=720)
            if ledger:
                ledger.save(result)
                write(ledger.events_path.parent / "sdk-stages.json", report.records)
            record = {**case, "report": result.model_dump(), "stages": report.records}
            rows = report.records
        elif case["kind"] == "chat":
            sid = hashlib.sha256((fingerprint(protocol) + case["group"]).encode()).hexdigest()[:32]
            if not store.path(sid).exists():
                if replay:
                    raise ValueError("missing saved conversation")
                state = store.create(case["snapshot_id"], session_id=sid, retrieval_config=protocol["retrieval"])
                state.update(engine="sdk", agent_limits=limits.model_dump(), agent_identity={
                    "config_id": runtime.settings.config_id, "index_id": index.index_id, "runtime": sdk_version()})
                store.save(state)
            if store.load(sid)["pending"] and not replay:
                record = {**case, "status": "blocked_by_failed_turn", "session_id": sid,
                          "new_model_requests": 0}
                write(path, record)
                print(json.dumps({"case": case["case_id"], "status": record["status"]}), flush=True)
                continue
            turn = await store.turn(sid, engine, case["question"], request_id=case["case_id"])
            record = {**case, "session_id": sid, "conversation_turn": turn, "result": turn["result"]}
            rows = [turn["result"]]
        else:
            row = await engine.answer(case["question"])
            record, rows = {**case, "result": row}, [row]
        requests = sum(r["new_model_requests"] for r in rows)
        if case["kind"] == "chat":
            requests = record["conversation_turn"]["new_model_requests"]
        if replay:
            original = load(path)
            if is_report:
                assert record["report"]["outputs"] == original["report"]["outputs"]
            else:
                assert record["result"]["attempt_id"] == original["result"]["attempt_id"]
            assert requests == 0
        else:
            write(path, record)
        receipt = {"case": case["case_id"], "new_model_requests": requests,
                   "statuses": [r["completion"]["status"] for r in rows]}
        receipts.append(receipt)
        print(json.dumps(receipt), flush=True)
    if replay:
        write(OUT / "replay.json", {"network_disabled": True, "new_model_requests": 0, "cases": receipts})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "replay"))
    parser.add_argument("--out", type=Path, default=OUT, help="new directory for a new implementation version")
    parser.add_argument("--selection", choices=("full", "budget-regression"), default="full")
    args = parser.parse_args()
    OUT, SELECTION = args.out.resolve(), args.selection
    if args.mode == "prepare":
        print(json.dumps({"cases": len(prepare()["cases"]), "out": str(OUT)}))
    else:
        asyncio.run(run(args.mode == "replay"))
