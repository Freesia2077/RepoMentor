"""CLI calls the report and evaluation cores directly without a web server."""

import argparse
import asyncio
import importlib.metadata
import json
import signal
import sys
from pathlib import Path
from uuid import uuid4

from .config import ConfigurationError, load_chat_settings, load_model_settings, write_json
from .execution import CancellationToken
from .paths import data_root, model_settings_path, python_home, runtime_dir
from .repository import SnapshotStore
from .storage import RunStore


def parser():
    root = argparse.ArgumentParser(
        prog="repomentor", description="RepoMentor Python: headless repository reports and evaluation"
    )
    root.add_argument(
        "--data-dir", type=Path, default=runtime_dir(), help="override this CLI run's storage directory"
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="print interpreter and SDK versions (no model calls)")
    snapshot = commands.add_parser("snapshot", help="freeze a Git repository")
    snapshot.add_argument("repository")
    snapshot.add_argument("--revision", default="HEAD")
    report = commands.add_parser("report", help="run the three compatibility report stages")
    target = report.add_mutually_exclusive_group(required=True)
    target.add_argument("--repository")
    target.add_argument("--snapshot-id")
    report.add_argument("--revision", default="HEAD")
    report.add_argument("--runtime", choices=("sdk", "claude", "stub", "local"), default="sdk")
    report.add_argument(
        "--settings", type=Path, help="explicit Python model settings JSON; overrides REPOMENTOR_PY_* sources"
    )
    report.add_argument("--timeout", type=float, default=900)
    report.add_argument("--call-timeout", type=float, help="override the model configuration's call timeout")
    report.add_argument("--answer-max-tokens", type=int, help="final output cap; SDK reports default to 2048")
    report.add_argument("--output", type=Path)
    report.add_argument("--refresh", action="store_true", help="explicitly rerun cached local report stages")
    report.add_argument("--agent-max-tools", type=int, help="SDK tool budget per report role (default: 6)")
    report.add_argument("--agent-max-decisions", type=int, help="SDK model decision budget per report role (default: 6)")
    report.add_argument("--agent-timeout", type=float, help="SDK deadline per report role in seconds (default: 300)")
    export = commands.add_parser("export", help="export a previously saved run, without re-execution")
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect-run", help="render a saved answer and source/trace as Markdown (offline)")
    inspect.add_argument("record", type=Path)
    inspect.add_argument("--case-id", help="select a saved case from benchmark JSONL")
    inspect.add_argument("--output", type=Path, help="write Markdown to a file; default: stdout")
    questions = commands.add_parser("questions", help="inspect the unified question catalog offline")
    questions.add_argument("action", nargs="?", choices=("stats", "list", "show", "verify", "rebuild"), default="stats")
    questions.add_argument("question_id", nargs="?")
    questions.add_argument("--scope", choices=("current", "all"), default="current")
    questions.add_argument("--repository", help="repository owner/name, e.g. pallets-eco/blinker")
    questions.add_argument("--status", choices=("reviewed", "generated", "retrieval_only", "registered_only"))
    questions.add_argument("--root", type=Path, default=python_home())
    questions.add_argument("--catalog", type=Path, default=python_home() / "experiments/question-bank.json")
    benchmark = commands.add_parser("benchmark", help="compare existing fixed RAG and SDK engines on the same public questions")
    benchmark.add_argument("action", choices=("prepare", "run", "summary", "review", "verify", "finalize"))
    benchmark.add_argument("--output", type=Path, required=True, help="explicit comparison directory")
    benchmark.add_argument("--conditions", nargs="+", default=["none", "bm25", "bm25_code", "hybrid_rerank", "agentic"])
    benchmark.add_argument("--oracle-count", type=int, default=12, help="source-sufficient diagnostics on development questions only")
    benchmark.add_argument("--agent-max-decisions", type=int, default=6)
    benchmark.add_argument("--retrieval-device", choices=("cpu", "cuda"), default="cuda")
    benchmark.add_argument("--top-k", type=int, default=6)
    benchmark.add_argument("--candidate-k", type=int, default=20)
    benchmark.add_argument("--chunk-size", type=int, default=768)
    benchmark.add_argument("--source-bytes", type=int, default=8000)
    benchmark.add_argument("--plan", type=Path, help="freeze an explicit bounded follow-up plan; embeds it in the same four-file suite")
    benchmark.add_argument("--authorized-new-requests", type=int, help="explicitly owner-authorized cap; prepare never generates answers")
    evaluation = commands.add_parser(
        "eval", help="sequential fixed-input JSONL evaluation with checkpoint cache"
    )
    evaluation.add_argument("--tasks", type=Path, required=True)
    evaluation.add_argument(
        "--settings", type=Path, help="Chat Completions profile with immutable model revision"
    )
    evaluation.add_argument("--output", type=Path)
    evaluation.add_argument("--limit", type=int)
    evaluation.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None)
    retry = evaluation.add_mutually_exclusive_group()
    retry.add_argument("--refresh", action="store_true", help="explicitly rerun all selected tasks")
    retry.add_argument("--retry-failed", action="store_true", help="retry failed cached records only")
    for name in ("index", "ask", "chat"):
        command = commands.add_parser(
            name,
            help={
                "index": "build a frozen repository retrieval index",
                "ask": "answer with local repository retrieval",
                "chat": "persistent repository conversation",
            }[name],
        )
        target = command.add_mutually_exclusive_group(required=name != "chat")
        target.add_argument("--repository")
        target.add_argument("--snapshot-id")
        command.add_argument("--revision", default="HEAD")
        command.add_argument("--chunker", choices=("line", "syntax"), default=None)
        command.add_argument(
            "--retriever", choices=("bm25", "vector", "hybrid", "hybrid-rerank"), default=None
        )
        command.add_argument("--retrieval-settings", type=Path)
        command.add_argument("--retrieval-device", choices=("cuda", "cpu"), default=None)
        command.add_argument("--code-aware", action=argparse.BooleanOptionalAction, default=None,
                             help="enable symbol and static-call retrieval augmentation")
        command.add_argument("--top-k", type=int, help="maximum initial source units (1-30)")
        command.add_argument("--candidate-k", type=int, help="retrieval candidate limit, at least top-k")
        command.add_argument("--chunk-size", type=int, help="syntax chunk limit in UTF-8 bytes (128-2048)")
        command.add_argument("--source-bytes", type=int, help="initial source byte allowance; total model window still applies")
        command.add_argument("--max-lines", type=int, help="line chunker maximum lines")
        command.add_argument("--overlap-lines", type=int, help="line chunker overlap, smaller than max-lines")
        command.add_argument(
            "--literal-query",
            action="store_true",
            default=None,
            help="disable recorded bilingual query expansion",
        )
        if name != "index":
            command.add_argument("--engine", choices=("sdk", "rag"), default=None,
                                 help="sdk (default tool-assisted research), rag (fixed retrieval comparison)")
            command.add_argument("--agent-max-tools", type=int, default=None)
            command.add_argument("--agent-specialists", choices=("off", "auto"), default=None,
                                 help="SDK experts share the same episode budget; off is the baseline")
            command.add_argument("--agent-max-decisions", type=int, default=None)
            command.add_argument("--agent-timeout", type=float, default=None)
            command.add_argument(
                "--settings",
                type=Path,
                help="explicit Chat Completions profile; overrides REPOMENTOR_PY_* and the default file",
            )
            command.add_argument("--output", type=Path)
            command.add_argument("--answer-max-tokens", type=int, help="override final answer allowance within the configured context")
        if name == "ask":
            command.add_argument("--question", required=True)
            command.add_argument("--mode", choices=("bm25", "none"), default="bm25")
            command.add_argument("--refresh", action="store_true")
        if name == "chat":
            command.add_argument("--session")
            command.add_argument("--message")
            command.add_argument("--request-id")
            command.add_argument("--resume", action="store_true")
            command.add_argument("--goal", default="理解仓库")
            command.add_argument("--history-mode", choices=("recent", "summary"), default=None)
            command.add_argument("--query-rewrite", choices=("none", "rules"), default=None)
    return root


def open_reader(args, snapshot_id=None):
    store = SnapshotStore(args.data_dir / "snapshots")
    selected = snapshot_id or args.snapshot_id
    if selected:
        try:
            return store.open(selected)
        except FileNotFoundError:
            return SnapshotStore(data_root() / "corpus").open(selected)
    return store.import_repository(args.repository, args.revision)


def print_answer(result):
    if result.get("answer") and result["completion"]["status"] == "success":
        print(result["answer"]["answer"])
        for ref in result["answer"]["citations"]:
            print(f"  {ref['path']}:{ref['start_line']}-{ref['end_line']}")
    else:
        print(
            json.dumps(
                {"status": result["completion"]["status"], "error": result["completion"]["error_code"]}
            )
        )
    requests = result.get("new_model_requests", 0)
    elapsed = result.get("elapsed_seconds", result["completion"].get("elapsed_seconds"))
    print(f"模型请求：{requests}；耗时：{elapsed} 秒；语义正确性需单独评审。")


async def rag_command(args):
    from .conversation import ConversationStore
    from .rag import RagEngine
    from .retrieval_factory import build_index
    from .runtimes.chat import ChatCompletionsRuntime

    store = ConversationStore(args.data_dir / "conversations") if args.command == "chat" else None
    state = store.load(args.session) if store and args.session else None
    if state and state.get("engine") not in (None, "sdk", "rag"):
        raise ConfigurationError("This session uses a removed engine; preserve it and create a new SDK session.")
    engine_name = getattr(args, "engine", None) or (state.get("engine", "rag") if state else "sdk")
    if state and engine_name != state.get("engine", "rag"):
        raise ValueError("session engine is pinned; create a new session to switch")
    if state and (args.repository or args.snapshot_id):
        raise ValueError("an existing session already pins its repository")
    if not state and not args.repository and not args.snapshot_id:
        raise ValueError("provide a repository or an existing session")
    reader = open_reader(args, state["snapshot_id"] if state else None)
    from .retrieval_policy import DEFAULT_RETRIEVAL, RetrievalPolicy
    defaults = (RetrievalPolicy() if args.command == "index" else DEFAULT_RETRIEVAL).model_dump()
    selected = {
        "chunker": args.chunker,
        "retriever": args.retriever,
        "device": args.retrieval_device,
        "query_expansion": not args.literal_query if args.literal_query is not None else None,
        **{name: getattr(args, name, None) for name in
           ("code_aware", "top_k", "candidate_k", "chunk_size", "source_bytes", "max_lines", "overlap_lines")},
    }
    pinned = {**defaults, **((state or {}).get("retrieval_config") or {})}
    if state and any(value is not None and value != pinned[key] for key, value in selected.items()):
        raise ValueError("session retrieval policy is pinned; create another session for an ablation")
    selected = {key: value if value is not None else pinned[key] for key, value in selected.items()}
    selected = RetrievalPolicy.model_validate(selected).model_dump()
    if state and (
        args.history_mode
        and args.history_mode != state.get("history_mode", "recent")
        or args.query_rewrite
        and args.query_rewrite != state.get("query_rewrite", "rules")
    ):
        raise ValueError("session memory/rewrite policy is pinned")
    profile_path = getattr(args, "settings", None)
    needs_profile = args.command != "index" or (
        selected["retriever"] != "bm25" and selected["device"] == "cuda" and (profile_path or model_settings_path()).exists()
    )
    profile = load_chat_settings(profile_path) if needs_profile else None
    if profile and getattr(args, "answer_max_tokens", None) is not None:
        from .runtimes.chat import ChatSettings
        profile = ChatSettings.model_validate({**profile.model_dump(), "max_tokens": args.answer_max_tokens})
    index = build_index(
        reader, storage=args.data_dir, retrieval_settings=args.retrieval_settings, profile=profile, **selected
    )
    if args.command == "index":
        if hasattr(index, "build_vectors") and selected["retriever"] != "bm25":
            try:
                index.build_vectors()
            finally:
                index.release_models()
        print(
            json.dumps(
                {
                    "index_id": index.index_id,
                    "snapshot_id": reader.snapshot.snapshot_id,
                    "chunks": len(index.chunks),
                    "files": index.record["files"],
                    "omissions": len(index.record["omissions"]),
                    "retrieval_policy": selected,
                }
            )
        )
        return 0
    if engine_name == "sdk":
        from .agent_tools import AgentLimits
        from .runtimes.openai_sdk import OpenAISdkRuntime
        from .sdk_agent import SdkAgentEngine, sdk_version
        from .sdk_collaboration import CollaborationSettings

        requested = {
            k: v
            for k, v in {
                "max_tool_calls": args.agent_max_tools,
                "max_decisions": args.agent_max_decisions,
                "timeout_seconds": args.agent_timeout,
            }.items()
            if v is not None
        }
        pinned_limits = (state or {}).get("agent_limits")
        if pinned_limits and any(pinned_limits[k] != v for k, v in requested.items()):
            raise ValueError("session agent limits are pinned")
        # Validate defaults too: integer and float timeouts must share CLI/batch cache keys.
        limits = AgentLimits(**(pinned_limits or {**AgentLimits().model_dump(), **requested}))
        mode = args.agent_specialists or (state or {}).get("collaboration_mode", "off")
        if state and mode != state.get("collaboration_mode", "off"):
            raise ValueError("session collaboration policy is pinned; create a new session")
        engine = SdkAgentEngine(reader, index, OpenAISdkRuntime(profile), args.data_dir / "sdk/cache", limits,
                                collaboration=CollaborationSettings(mode=mode))
        identity = {"config_id": engine.runtime.settings.config_id, "index_id": index.index_id,
                    "runtime": sdk_version()}
        if state and state.get("agent_identity") != identity:
            raise ValueError("session agent model/index identity changed; create a new session")
    else:
        if any(
            getattr(args, key, None) is not None
            for key in ("agent_max_tools", "agent_max_decisions", "agent_timeout", "agent_specialists")
        ):
            raise ValueError("agent limits require --engine sdk")
        engine = RagEngine(reader, index, ChatCompletionsRuntime(profile), args.data_dir / "rag/cache")
    if store and not state:
        state = store.create(
            reader.snapshot.snapshot_id,
            args.goal,
            history_mode=args.history_mode or "recent",
            query_rewrite=args.query_rewrite or "rules",
            retrieval_config=selected,
        )
        if engine_name == "sdk":
            state.update(engine=engine_name, agent_limits=limits.model_dump(), agent_identity=identity)
            state["collaboration_mode"] = mode
            store.save(state)
    if state:
        print(
            json.dumps({"session_id": state["session_id"], "snapshot_id": state["snapshot_id"]}), flush=True
        )
    interactive = args.command == "chat" and args.message is None and not args.resume
    while True:
        if interactive:
            try:
                question = input("RepoMentor > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if question in ("/exit", "/quit"):
                return 0
            if not question:
                continue
        else:
            question = args.question if args.command == "ask" else args.message
        token = CancellationToken()
        previous = signal.signal(signal.SIGINT, lambda *_: token.cancel())
        try:
            if args.command == "ask":
                if engine_name != "rag" and args.mode != "bm25":
                    raise ValueError("--mode none applies only to the fixed RAG engine")
                options = {"mode": args.mode} if engine_name == "rag" else {}
                result = await engine.answer(question, token=token, refresh=args.refresh, **options)
                document = result
            else:
                document = await store.turn(
                    state["session_id"],
                    engine,
                    None if interactive and question == "/resume" else question,
                    request_id=args.request_id,
                    token=token,
                    resume=args.resume or (interactive and question == "/resume"),
                )
                result = document["result"]
        finally:
            signal.signal(signal.SIGINT, previous)
        result["retrieval_policy"] = selected
        output = args.output or args.data_dir / f"{engine_name}/results" / (result["attempt_id"] + ".json")
        write_json(output, document)
        print_answer(result)
        print(
            json.dumps(
                {
                    "result": str(output.resolve()),
                    "cache_hit": result["cache_hit"],
                    "new_model_requests": document.get("new_model_requests", 0),
                }
            ),
            flush=True,
        )
        if not interactive:
            return 0 if result["completion"]["status"] == "success" else 130 if token.cancelled else 1


async def async_main(args):
    if args.command == "benchmark":
        from .benchmark import command

        await command(args)
        return 0
    if args.command == "questions":
        from .question_bank import run_command

        return run_command(args)
    if args.command == "inspect-run":
        from .presentation import render_run
        from .records import atomic_write
        raw = args.record.read_text(encoding="utf-8")
        rows = [json.loads(s) for s in raw.splitlines() if s.strip()] if args.record.suffix == ".jsonl" else [json.loads(raw)]
        matches = [r for r in rows if not args.case_id or r.get("case_id") == args.case_id]
        if len(matches) != 1:
            raise ConfigurationError("select exactly one saved case with --case-id; use questions show to find case IDs")
        rendered = render_run(matches[0])
        if args.output:
            atomic_write(args.output, rendered)
            print(str(args.output.resolve()))
        else:
            print(rendered)
        return 0
    if args.command in ("index", "ask", "chat"):
        return await rag_command(args)
    if args.command == "doctor":
        from claude_agent_sdk._cli_version import __cli_version__

        print(
            json.dumps(
                {
                    "executable": sys.executable,
                    "prefix": sys.prefix,
                    "user_site_disabled": bool(sys.flags.no_user_site),
                    "python": sys.version,
                    "sdk": importlib.metadata.version("claude-agent-sdk"),
                    "openai_agents_sdk": importlib.metadata.version("openai-agents"),
                    "openai": importlib.metadata.version("openai"),
                    "bundled_cli": __cli_version__,
                    "python_home": str(python_home()),
                    "settings_path": str(model_settings_path()),
                    "settings_file_exists": model_settings_path().is_file(),
                    "data_root": str(data_root()),
                    "runtime_dir": str(args.data_dir.resolve()),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "export":
        value = RunStore.load_saved(args.data_dir, args.run_id)
        if value is None:
            raise ValueError("unknown run ID")
        write_json(args.output, value)
        print(args.output)
        return 0
    if args.command == "eval":
        from .evaluation import load_tasks, run_evaluation
        from .runtimes.chat import ChatCompletionsRuntime

        settings = load_chat_settings(args.settings)
        if args.stream is not None:
            settings.stream = args.stream
        tasks = load_tasks(args.tasks, args.limit)
        output = args.output or args.data_dir / "eval/results" / settings.config_id / "results.jsonl"
        if output.resolve() == args.tasks.resolve():
            raise ValueError("result output must not replace the task input")
        token = CancellationToken()
        previous = signal.signal(signal.SIGINT, lambda *_: token.cancel())
        try:
            summary = await run_evaluation(
                tasks,
                ChatCompletionsRuntime(settings),
                snapshots=SnapshotStore(data_root() / "corpus"),
                cache_dir=args.data_dir / "eval/cache",
                output=output,
                token=token,
                refresh=args.refresh,
                retry_failed=args.retry_failed,
                emit=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True),
            )
            print(json.dumps({**summary, "output": str(output.resolve())}, ensure_ascii=False))
            return 130 if summary["cancelled"] else 0 if set(summary["statuses"]) <= {"success"} else 1
        finally:
            signal.signal(signal.SIGINT, previous)
    settings = None
    report_limits = None
    if args.command == "report":
        if args.timeout <= 0 or (args.call_timeout is not None and args.call_timeout <= 0):
            raise ValueError("timeouts must be positive")
        if args.runtime != "sdk" and any(v is not None for v in (
            args.agent_max_tools, args.agent_max_decisions, args.agent_timeout,
        )):
            raise ValueError("agent budgets apply only to the SDK report runtime")
        if args.runtime == "sdk":
            from .agent_tools import AgentLimits

            report_limits = AgentLimits(
                max_tool_calls=6 if args.agent_max_tools is None else args.agent_max_tools,
                max_decisions=6 if args.agent_max_decisions is None else args.agent_max_decisions,
                timeout_seconds=300 if args.agent_timeout is None else args.agent_timeout,
            )
        if args.runtime == "claude":
            # Fail before any clone or run-directory creation when configuration is missing.
            settings = load_model_settings(args.settings)
            if args.call_timeout is not None:
                settings.timeout_seconds = args.call_timeout
        elif args.runtime in ("local", "sdk"):
            settings = load_chat_settings(args.settings)
            # Reports need more output than short QA; budget and configuration ID remain explicit.
            from .runtimes.chat import ChatSettings
            settings = ChatSettings.model_validate({**settings.model_dump(),
                "max_tokens": args.answer_max_tokens or 2048})
            if args.call_timeout is not None:
                settings.timeout_seconds = args.call_timeout
    snapshots = SnapshotStore(args.data_dir / "snapshots")
    if args.command == "snapshot":
        reader = snapshots.import_repository(args.repository, args.revision)
        print(reader.snapshot.model_dump_json(indent=2))
        return 0
    reader = open_reader(args)
    run_id = uuid4().hex
    store = RunStore(args.data_dir, run_id)
    token = CancellationToken()
    previous = signal.signal(signal.SIGINT, lambda *_: token.cancel())
    try:
        if args.runtime == "claude":
            from .runtimes.claude import ClaudeSdkRuntime

            runtime = ClaudeSdkRuntime(settings, emit=store.emit)
            store.emit("report", "configuration", settings.public())
        elif args.runtime == "sdk":
            from .retrieval_factory import build_index
            from .runtimes.openai_sdk import OpenAISdkRuntime
            from .sdk_report import SdkReport

            index = build_index(reader, storage=args.data_dir, profile=settings, token=token)
            runtime = SdkReport(reader, index, OpenAISdkRuntime(settings),
                                args.data_dir / "sdk/cache", refresh=args.refresh, limits=report_limits)
        elif args.runtime == "local":
            from .retrieval import BM25Index
            from .runtimes.chat import ChatCompletionsRuntime
            from .runtimes.local_report import LocalReportRuntime

            index = BM25Index.build(reader, args.data_dir / "indexes", token=token)
            runtime = LocalReportRuntime(
                reader,
                index,
                ChatCompletionsRuntime(settings),
                args.data_dir / "rag/cache",
                refresh=args.refresh,
            )
        else:
            from .runtimes.stub import StubRuntime

            runtime = StubRuntime()
        from .report import run_report

        if args.runtime == "sdk":
            result = await runtime.run(emit=store.emit, token=token, run_id=run_id, total_timeout=args.timeout)
        else:
            result = await run_report(
                reader, runtime, emit=store.emit, token=token, run_id=run_id, total_timeout=args.timeout
            )
        path = store.save(result)
        if args.runtime == "local":
            write_json(path.parent / "local-stages.json", runtime.records)
        if args.runtime == "sdk":
            write_json(path.parent / "sdk-stages.json", runtime.records)
        if args.output:
            write_json(args.output, result.model_dump())
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": result.status,
                    "stop_reason": result.stop_reason,
                    "result": str(path.resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.status == "success" else 130 if result.status == "cancelled" else 1
    finally:
        signal.signal(signal.SIGINT, previous)


def main():
    args = parser().parse_args()
    try:
        code = asyncio.run(async_main(args))
    except Exception as exc:
        # Configuration failures do not dump potentially credential-bearing values/validation inputs.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": getattr(exc, "code", "configuration_error"),
                    "error_type": type(exc).__name__,
                    **({"message": str(exc)} if isinstance(exc, ConfigurationError) else {}),
                }
            ),
            file=sys.stderr,
        )
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
