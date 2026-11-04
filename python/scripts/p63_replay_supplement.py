"""Replay recorded conversations and report stages with generation explicitly forbidden."""

import asyncio
from types import SimpleNamespace

from p63 import load, open_task, runtime, settings
from p63_dataset import OUT, prepare

from repomentor.agent_tools import AgentLimits
from repomentor.conversation import ConversationStore
from repomentor.paths import runtime_dir
from repomentor.records import write
from repomentor.retrieval_factory import build_index
from repomentor.sdk_agent import sdk_version
from repomentor.sdk_report import SdkReport


async def main():
    if load(OUT / "runs/reports-v1/manifest.json")["sdk"] != sdk_version():
        raise ValueError("SDK identity changed since this batch; preserve its replay receipt and use 'p63.py verify' for offline result verification. No regeneration is allowed by replay.")
    store = ConversationStore(runtime_dir() / "p63/conversations")
    folder = OUT / "runs/dialogues-v1"
    receipts = []
    for saved in sorted(folder.glob("*-session.json")):
        state = load(saved)
        engine = SimpleNamespace(reader=SimpleNamespace(snapshot=SimpleNamespace(snapshot_id=state["snapshot_id"])))
        async def forbidden(*args, **kwargs):
            raise AssertionError("conversation replay must not call the engine")
        engine.answer = forbidden
        for turn in state["turns"]:
            # Session IDs are stored with each case, independent of serialized session shape.
            case = next(load(p) for p in (folder / "cases").glob("*.json")
                        if load(p).get("request_id") == turn["request_id"])
            result = await store.turn(case["session_id"], engine, turn["question"], request_id=turn["request_id"])
            assert result["replayed"] and result["new_model_requests"] == 0
            assert result["result"]["attempt_id"] == turn["result"]["attempt_id"]
            receipts.append({"request_id": turn["request_id"], "requests": 0})
    write(folder / "replay.json", {"cases": receipts, "generation_requests": 0, "metadata_requests": 0,
        "scope": "Completed conversation request IDs only. Failed pending turns are retained, not retried."})
    folder, protocol, receipts = OUT / "runs/reports-v1", prepare(), []
    selection = load(OUT / "selection.json")
    for saved in sorted((folder / "cases").glob("*.json")):
        case = load(saved)
        reader = open_task(case)
        index = build_index(reader, retriever=selection["retriever"], device=selection["device"], profile=settings())
        report = SdkReport(reader, index, runtime("sdk", protocol, "report-replay", replay=True),
            runtime_dir() / "p63/sdk-cache", limits=AgentLimits(max_decisions=5, max_tool_calls=4, timeout_seconds=360))
        result = await report.run(total_timeout=1080)
        assert result.status == case["report"]["status"]
        assert len(report.records) == len(case["stages"])
        for current, old in zip(report.records, case["stages"], strict=True):
            assert current["new_model_requests"] == 0 and current["attempt_id"] == old["attempt_id"]
            assert current["answer"] == old["answer"]
            receipts.append({"repository": case["repository"], "stage": current["stage"], "requests": 0})
    write(folder / "replay.json", {"cases": receipts, "generation_requests": 0, "metadata_requests": 0})
    print("Conversation and report cache replay: generation 0, model metadata 0.")


if __name__ == "__main__":
    asyncio.run(main())
