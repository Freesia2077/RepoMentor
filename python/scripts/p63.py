"""Unified P6.3 commands; generation runner bytes remain frozen independently."""

import asyncio
import sys

from p63_runner import ledger, load, open_task, runtime, settings, verify_frozen  # noqa: F401


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "--help"
    if action == "verify-archive":
        from verify_archives import verify
        verify()
    elif action == "verify":
        from verify_p63_delivery import verify
        verify()
    elif action == "quality-audit":
        from p63_quality_audit import audit
        audit()
    elif action == "closeout":
        from p63_closeout import main as closeout
        sys.argv.pop(1)
        closeout()
    elif action == "closeout-demo":
        from p63_closeout_demo import main as demo
        asyncio.run(demo("--replay" in sys.argv))
    elif action == "closeout-review":
        from p63_closeout_review import audit, export, finalize
        {"export": export, "finalize": finalize, "audit": audit}[sys.argv[2]]()
    elif action == "quality":
        from p63_quality import main as quality
        sys.argv.pop(1)
        quality()
    elif action == "model-probe":
        raise SystemExit("The 4B/9B probe is archived. Use verify-archive or inspect-run; "
                         "new 9B runs use repomentor.cli with config/model-settings.json.")
    elif action == "reliability":
        from qwen9b_reliability import main as reliability
        sys.argv.pop(1)
        reliability()
    elif action == "followup":
        from qwen9b_followup import main as followup
        sys.argv.pop(1)
        followup()
    elif action == "delivery-report":
        from p63_delivery_report import main as render
        render()
    elif action == "cache-probe":
        from p63_cache_probe import main as probe
        probe()
    elif action == "tool-demo":
        from p63_tool_demo import main as demo
        demo()
    elif action == "replay-supplement":
        from p63_replay_supplement import main as replay
        asyncio.run(replay())
    else:
        if action in {"--help", "-h"}:
            print("Additional offline commands: verify, delivery-report, tool-demo, quality-audit. "
                  "Local retrieval benchmark: cache-probe. Completed conversation/report replay: replay-supplement.")
        from p63_runner import main as run
        run()


if __name__ == "__main__":
    main()
