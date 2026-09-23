"""CLI calls the core directly. P3+ commands are added when implemented, not placeholders."""

import argparse
import asyncio
import importlib.metadata
import json
import signal
import sys
from pathlib import Path
from uuid import uuid4

from .config import ConfigurationError, load_model_settings, write_json
from .harness import CancellationToken
from .paths import data_root, model_settings_path, python_home, runtime_dir
from .repository import SnapshotStore
from .storage import RunStore


def parser():
    root = argparse.ArgumentParser(
        prog="repomentor", description="RepoMentor Python: headless repository reports"
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
    report.add_argument("--runtime", choices=("claude", "stub"), default="claude")
    report.add_argument(
        "--settings", type=Path, help="explicit Python model settings JSON; overrides REPOMENTOR_PY_* sources"
    )
    report.add_argument("--timeout", type=float, default=600)
    report.add_argument("--call-timeout", type=float, help="override the model configuration's call timeout")
    report.add_argument("--output", type=Path)
    export = commands.add_parser("export", help="export a previously saved run, without re-execution")
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True)
    return root


async def async_main(args):
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
    settings = None
    if args.command == "report":
        if args.timeout <= 0 or (args.call_timeout is not None and args.call_timeout <= 0):
            raise ValueError("timeouts must be positive")
        if args.runtime == "claude":
            # Fail before any clone or run-directory creation when configuration is missing.
            settings = load_model_settings(args.settings)
            if args.call_timeout is not None:
                settings.timeout_seconds = args.call_timeout
    snapshots = SnapshotStore(args.data_dir / "snapshots")
    if args.command == "snapshot":
        reader = snapshots.import_repository(args.repository, args.revision)
        print(reader.snapshot.model_dump_json(indent=2))
        return 0
    reader = (
        snapshots.open(args.snapshot_id)
        if args.snapshot_id
        else snapshots.import_repository(args.repository, args.revision)
    )
    run_id = uuid4().hex
    store = RunStore(args.data_dir, run_id)
    token = CancellationToken()
    previous = signal.signal(signal.SIGINT, lambda *_: token.cancel())
    try:
        if args.runtime == "claude":
            from .runtimes.claude import ClaudeSdkRuntime

            runtime = ClaudeSdkRuntime(settings, emit=store.emit)
            store.emit("report", "configuration", settings.public())
        else:
            from .runtimes.stub import StubRuntime

            runtime = StubRuntime()
        from .report import run_report

        result = await run_report(
            reader, runtime, emit=store.emit, token=token, run_id=run_id, total_timeout=args.timeout
        )
        path = store.save(result)
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
