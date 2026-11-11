"""Thin TRL environment and reward adapter around pinned SWE-QA-Pro implementations."""

# 适配 SWE-QA-Pro 三类工具、Linux 只读隔离、模型评审及费用账本。
# 每条采样轨迹独立记录来源、工具尝试和评分，评测采集模式可以只保存答案。
import ast
import json
import os
import posixpath
import re
import runpy
import shutil
import signal
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from .core import ROOT, load_json

UTILS = ROOT / ".cache/upstream/train/RL/verl-tool/verl_tool/servers/tools/utils/swe_qa_pro_utils.py"
REWARD = ROOT / ".cache/upstream/train/RL/verl-tool/verl_tool/workers/reward_manager/swe_qa_pro.py"
WEIGHTS = {"correctness": 0.3, "completeness": 0.2, "relevance": 0.2, "clarity": 0.1, "reasoning": 0.2}
_CHROOT_LOCK = threading.Lock()
_truncate_text = runpy.run_path(str(UTILS))["truncate_text"]


def chroot_root(snapshot):
    """Container fallback: root-owned source copy and system tools, without host credentials."""
    base = ROOT / ".cache/tool-root"
    target = ROOT / ".cache/chroots" / snapshot.name
    with _CHROOT_LOCK:
        if (target / ".ready").is_file():
            if load_json(target / "repo/.snapshot.json") != load_json(snapshot / ".snapshot.json"):
                raise ValueError("Chroot snapshot identity changed")
            return target
        if not (base / ".ready").is_file():
            base.mkdir(parents=True, exist_ok=True)

            def include(path):
                path = Path(path)
                dest = base / path.relative_to("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(path.resolve(), dest)

            system = "/usr/bin/python3"
            stdlib = Path(
                subprocess.check_output(
                    [system, "-I", "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))"], text=True
                ).strip()
            )
            shutil.copytree(
                stdlib,
                base / stdlib.relative_to("/"),
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "ensurepip"),
            )
            seccomp = subprocess.check_output(
                [system, "-I", "-c", "import seccomp; print(seccomp.__file__)"], text=True
            ).strip()
            tree = ast.parse(UTILS.read_text(encoding="utf-8"))
            allowed = ast.literal_eval(
                next(
                    n.value
                    for n in tree.body
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "ALLOWED_BASH_COMMANDS" for t in n.targets)
                )
            )
            paths = [Path(system), Path("/bin/sh"), Path(seccomp)] + list(
                stdlib.glob("lib-dynload/_ctypes*.so")
            )
            paths += [Path(shutil.which(name)) for name in allowed if shutil.which(name)]
            for path in paths:
                include(path)
                result = subprocess.run(["ldd", str(path.resolve())], capture_output=True, text=True)
                for library in re.findall(r"(/[^\s()]+)", result.stdout):
                    if Path(library).is_file():
                        include(library)
            shutil.copy2(UTILS, base / "tool.py")
            for path in base.rglob("*"):
                path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
            base.chmod(0o555)
            (base / ".ready").touch()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(base, target, copy_function=os.link, ignore=shutil.ignore_patterns(".ready"))
        shutil.copytree(snapshot, target / "repo")
        # 隔离副本使用独立 inode，调整副本权限时保留原快照的内容与权限。
        for path in (target / "repo").rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        (target / "repo").chmod(0o555)
        (target / ".ready").touch()
    return target


_CHROOT_RUNNER = """
import ctypes,errno,json,os,resource,runpy,seccomp,sys
ctypes.CDLL(None).prctl(38,1,0,0,0)
resource.setrlimit(resource.RLIMIT_CPU,(10,10))
resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
resource.setrlimit(resource.RLIMIT_NPROC,(32,32))
policy=seccomp.SyscallFilter(defaction=seccomp.ALLOW)
for syscall in ['socket','socketpair','connect','bind','listen','accept','accept4','setsid','setpgid']:
    policy.add_rule(seccomp.ERRNO(errno.EPERM),syscall)
policy.load()
os.chdir('/repo')
functions=runpy.run_path('/tool.py')
print(json.dumps(functions[sys.argv[1]](**json.loads(sys.argv[2])),ensure_ascii=False))
"""


class JudgeBudget:
    """Reserve a conservative peak-price cost before each paid request, including retries."""

    def __init__(self, path, limit=9.0):
        self.path, self.limit = Path(path), limit

    # 请求发送前持久化保守费用预留，异常或续训仍沿用同一账本，避免重复预算失控。
    def reserve(self, messages):
        ledger = load_json(self.path) if self.path.exists() else {"accounted_upper_cny": 0.0, "requests": []}
        # 用 UTF-8 字节数保守估计文本 token，再为消息封装和最大输出预留费用。
        text_bytes = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 1024
        amount = (text_bytes * 2 + 512 * 8) / 1_000_000
        if ledger["accounted_upper_cny"] + amount > self.limit:
            raise RuntimeError("GRPO judge budget exhausted; no request sent")
        ticket = len(ledger["requests"])
        ledger["requests"].append(
            {"reserved_cny": amount, "accounted_upper_cny": amount, "time": time.time()}
        )
        ledger["accounted_upper_cny"] += amount
        self._save(ledger)
        return ticket

    def settle(self, ticket, usage, model):
        ledger = load_json(self.path)
        entry = ledger["requests"][ticket]
        entry.update(usage=usage, response_model=model)
        if usage:
            amount = (usage["prompt_tokens"] * 2 + usage["completion_tokens"] * 8) / 1_000_000
            if amount > entry["reserved_cny"]:
                raise RuntimeError("Judge token usage exceeded its conservative reservation")
            ledger["accounted_upper_cny"] += amount - entry["accounted_upper_cny"]
            entry["accounted_upper_cny"] = amount
        self._save(ledger)

    def _save(self, ledger):
        ledger.update(
            limit_cny=self.limit,
            pricing="Conservative peak: 2 CNY/M input, 8 CNY/M output; errors retain reservation",
            pricing_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


# 仅提取上游评分和答案解析方法，复用量表与重试逻辑，同时避开旧运行时和隐式 .env 读取。
def _judge_class():
    tree = ast.parse(REWARD.read_text(encoding="utf-8"))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SWEQAProRewardManager")
    methods = [
        n
        for n in original.body
        if isinstance(n, ast.FunctionDef) and n.name in {"_extract_answer", "_llm_score"}
    ]
    cls = ast.ClassDef(
        name="OfficialJudge", bases=[], keywords=[], body=methods, decorator_list=[], type_params=[]
    )
    namespace = {
        "re": re,
        "json": json,
        "_llm_lock": threading.Lock(),
        "SCORE_KEYS": tuple(WEIGHTS),
        "_FINISH_RE": re.compile(r"<finish>(.*?)</finish>", re.S),
    }
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(REWARD), "exec"),
        namespace,
    )
    return namespace["OfficialJudge"]


# 在隔离命名空间中只读挂载固定仓库并禁用网络；工具代码沿用已锁定的上游实现。
def sandbox_call(snapshot: Path, name: str, arguments: dict):
    """Run unmodified upstream helpers in a Linux read-only, networkless namespace."""
    if os.name != "posix":
        raise RuntimeError("Online tools require the Linux cloud environment")
    if os.environ.get("REPOMENTOR_PY_SFTGRPO_SANDBOX") == "chroot":
        root = chroot_root(snapshot)
        # RLIMIT_NPROC 按真实 UID 汇总进程，容器外同 UID 也计入；使用独立 UID 避免共享配额。
        command = [
            "timeout",
            "-s",
            "KILL",
            "12",
            "chroot",
            "--userspec=61123:61123",
            "--groups=61123",
            str(root),
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            _CHROOT_RUNNER,
            name,
            json.dumps(arguments, ensure_ascii=False),
        ]
        return _isolated_result(command)
    if not shutil.which("bwrap"):
        raise RuntimeError(
            "Online tools require Linux bubblewrap; run cloud preflight before loading weights"
        )
    command = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for directory in ["/usr", "/bin", "/lib", "/lib64", "/etc/alternatives"]:
        if Path(directory).exists():
            command += ["--ro-bind", directory, directory]
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(snapshot),
        "/repo",
        "--ro-bind",
        str(UTILS),
        "/tool.py",
        "--chdir",
        "/repo",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "/usr/bin/python3",
        "-I",
        "-c",
        "import runpy,json,sys; f=runpy.run_path('/tool.py'); print(json.dumps(f[sys.argv[1]](**json.loads(sys.argv[2])),ensure_ascii=False))",
        name,
        json.dumps(arguments, ensure_ascii=False),
    ]
    return _isolated_result(command)


# 超时时终止整个子进程组并收集输出，防止壳进程退出后仍残留工具子进程。
def _isolated_result(command):
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        start_new_session=True,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    if process.returncode:
        raise RuntimeError(f"Isolated tool failed: {stderr[-1000:]}")
    return json.loads(stdout)


class RepoEnvironment:
    """Per-rollout state; TRL owns tool dispatch, model generation and observation masks."""

    def __init__(self, runner=sandbox_call):
        self._runner = runner
        self.events = []
        self.tool_turn_limit = load_json(ROOT / "run-config.json")["rollout"]["max_tool_calling_iterations"]

    # 每条候选建立独立工具轨迹，先核对仓库、提交和完整展开标记，再允许读取。
    def reset(self, repo, commit, task_id, **kwargs):
        self.events = []
        self.task_id = task_id
        self.question = kwargs.get("question", "")
        self.snapshot = ROOT / ".cache/repos" / (repo.replace("/", "--") + "--" + commit)
        marker = load_json(self.snapshot / ".snapshot.json")
        if marker["repo"] != repo or marker["commit"] != commit:
            raise ValueError("Snapshot identity mismatch")
        if not marker.get("materialized_complete"):
            raise ValueError("Re-extract the canonical source archive on Linux: python prepare.py snapshots")
        self.source_identity = {key: marker[key] for key in ["repo", "commit", "archive_sha256"]}

    # 规范化路径后用工具名和参数识别精确重复；每次请求记账，已有观察的重复请求跳过执行。
    def _call(self, name, arguments):
        started = time.monotonic()
        event = {"tool": name, "arguments": arguments, "executed": False}
        try:
            if "path" in arguments:
                path = posixpath.normpath(posixpath.join("/repo", arguments["path"]))
                if path != "/repo" and not path.startswith("/repo/"):
                    raise ValueError("Path is outside the selected repository")
                if path == "/repo/repo" or path.startswith("/repo/repo/"):
                    alias = "/repo" + path[len("/repo/repo") :]
                    if (
                        not (self.snapshot / posixpath.relpath(path, "/repo")).exists()
                        and (self.snapshot / posixpath.relpath(alias, "/repo")).exists()
                    ):
                        path = alias
                arguments["path"] = path
            previous = next(
                (
                    i + 1
                    for i, item in enumerate(self.events)
                    if item["tool"] == name and item["arguments"] == arguments and "observation" in item
                ),
                None,
            )
            event["executed"] = previous is None
            # 固定快照与已有观察保持可见；精确重复请求返回去重提示，原始观察只保留一份。
            raw = self._runner(self.snapshot, name, arguments) if previous is None else None
            if name == "execute_readonly_command" and isinstance(raw, str):
                event["raw_observation_characters"] = len(raw)
                raw = _truncate_text(raw)
            value = (
                {"duplicate_of": previous, "result": "No new evidence; use the earlier tool response."}
                if previous is not None
                else {"view_content": raw}
                if name == "view_codebase"
                else {"command_output": raw}
                if name == "execute_readonly_command"
                else raw
            )
            used = len(self.events) + 1
            notices = []
            if name == "execute_readonly_command" and isinstance(raw, str):
                if raw.startswith("ERROR: Command contains a forbidden operator or command:"):
                    event["failure_kind"] = "upstream_command_guard"
                    notices.append(
                        "This shell form is rejected by the upstream read-only guard. It forbids find -exec, "
                        "xargs and redirection, and also rejects 'exec' in quoted search terms. "
                        "Reordering the same expression will not fix this. Use semantic_search with a plain term "
                        "and repository path, or inspect the file with view_codebase."
                    )
                elif (
                    raw.startswith("ERROR: Command execution failed")
                    and "(no output)" in raw
                    and "(no error message)" in raw
                ):
                    event["failure_kind"] = "empty_nonzero_exit"
                    notices.append(
                        "The command returned nonzero without output or an error message. For grep this can mean "
                        "no match, rather than an execution fault. Try a different term or semantic_search; "
                        "do not repeat the identical query."
                    )
            if previous is not None:
                event["duplicate_of"] = previous
                notices.append(
                    f"This repeats tool call {previous} with identical arguments on an unchanged repository. "
                    "Use the evidence already obtained; do not repeat this call. "
                )
            if self.tool_turn_limit and used >= min(20, self.tool_turn_limit - 5):
                notices.append(
                    f"You have used {used} tool calls; execution is limited to {self.tool_turn_limit} tool rounds. "
                    "Prepare your final answer from the evidence already observed. "
                    "Use further tools only for a specific missing fact. Answer inside <finish>...</finish>. "
                    "State any uncertainty; do not invent missing evidence."
                )
            if notices:
                value = {
                    **value,
                    "runtime_notice": "\n".join(notices) + f"\nOriginal question: {self.question}",
                }
                event["followup_message"] = {"role": "user", "content": value["runtime_notice"]}
            observation = json.dumps(value, ensure_ascii=False)
            event.update(
                observation=observation,
                observation_characters=len(observation),
                success=self.events[previous - 1]["success"]
                if previous is not None
                else not (isinstance(raw, str) and raw.startswith("ERROR:"))
                and not (isinstance(raw, dict) and raw.get("success") is False),
            )
            return observation
        except Exception as error:
            event.update(success=False, error=str(error))
            raise
        finally:
            event["seconds"] = time.monotonic() - started
            self.events.append(event)

    def view_codebase(
        self, path: str, view_range: list[int] | None = None, concise: bool = False, python_only: bool = False
    ) -> str:
        """Inspect a file, directory, or Python structural outline.

        Args:
            path: File or directory path in /repo.
            view_range: Optional [start, end], 1-based; end=-1 reads to the end.
            concise: Return Python signatures and major structure.
            python_only: Restrict to Python files.
        """
        if view_range is not None and len(view_range) != 2:
            raise ValueError("view_range needs exactly two integers")
        return self._call(
            "view_codebase", dict(path=path, view_range=view_range, concise=concise, python_only=python_only)
        )

    # 沿用公开工具协议名称，具体操作是普通子串查找；返回候选位置后再按需读取源码。
    def semantic_search(
        self,
        term: str,
        path: str = "/repo",
        python_only: bool = False,
        max_files: int = 100,
        include_lines: bool = False,
    ) -> str:
        """Find files containing a plain substring, without returning full files.

        Args:
            term: Substring to search for.
            path: File or directory in /repo.
            python_only: Restrict to .py files.
            max_files: Maximum matching files.
            include_lines: Include matching lines for a single file.
        """
        return self._call(
            "semantic_search",
            dict(
                term=term,
                path=path,
                python_only=python_only,
                max_files=max_files,
                include_lines=include_lines,
            ),
        )

    def execute_readonly_command(self, command: str) -> str:
        """Run a read-only shell inspection command in the isolated repository.

        Args:
            command: Inspection command such as grep, find, cat, or a read-only pipeline.
        """
        return self._call("execute_readonly_command", {"command": command})


class JudgeReward:
    """Official quality rubric with optional evidence gates and bounded redundancy shaping."""

    def __init__(self, output, client=None, model=None, collect_only=False, budget=None, policy=None):
        self.collect_only, self.budget_exhausted = collect_only, False
        self.policy = {**load_json(ROOT / "run-config.json")["reward"], **(policy or {})}
        if self.policy["mode"] not in {"quality", "grounded"}:
            raise ValueError("Unknown reward mode")
        if (
            not 0 <= self.policy["error_reward_cap"] <= self.policy["min_quality"] <= 1
            or not 1 <= self.policy["min_correctness"] <= 10
            or not 0 <= self.policy["duplicate_penalty"] <= self.policy["max_redundancy_penalty"] <= 1
            or self.policy["max_evidence_characters"] <= 0
        ):
            raise ValueError("Invalid reward gate or bounded-penalty configuration")
        self.active_evidence = self.grounding = None
        model = None if collect_only else model or os.environ["REPOMENTOR_PY_SFTGRPO_JUDGE_MODEL"]
        if client is None and not collect_only:
            from openai import OpenAI

            key = os.environ["REPOMENTOR_PY_SFTGRPO_JUDGE_API_KEY"]
            base = os.environ.get("REPOMENTOR_PY_SFTGRPO_JUDGE_BASE_URL", "https://api.deepseek.com")
            if base.rstrip("/") not in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
                raise ValueError("This price budget is valid only for the official DeepSeek endpoint")
            if model not in {"deepseek-flash", "deepseek-v4-flash"}:
                raise ValueError("This price budget requires the DeepSeek Flash model")
            client = OpenAI(
                api_key=key,
                base_url=base,
                max_retries=0,
                timeout=60,
                default_headers={"Accept-Encoding": "gzip, deflate"},
            )
            budget = budget or JudgeBudget(ROOT / "outputs/judge-budget.json")
        self.attempts = []

        def recorded_create(**kwargs):
            from .rewards import grounded_messages, parse_grounding

            started = time.monotonic()
            event = {}
            try:
                if self.policy["mode"] == "grounded":
                    kwargs["messages"] = grounded_messages(kwargs["messages"], self.active_evidence)
                kwargs.update(max_tokens=512, extra_body={"thinking": {"type": "disabled"}})
                ticket = None
                if budget:
                    try:
                        ticket = budget.reserve(kwargs["messages"])
                    except RuntimeError:
                        self.budget_exhausted = True
                        raise
                response = client.chat.completions.create(**kwargs)
                usage = getattr(response, "usage", None)
                event["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else None
                event["response_model"] = getattr(response, "model", None)
                if budget:
                    try:
                        budget.settle(ticket, event["usage"], event["response_model"])
                    except RuntimeError:
                        self.budget_exhausted = True
                        raise
                if self.policy["mode"] == "grounded":
                    self.grounding = parse_grounding(response.choices[0].message.content)
                return response
            except Exception as error:
                event["error_type"] = type(error).__name__
                raise
            finally:
                event["seconds"] = time.monotonic() - started
                self.attempts.append(event)

        self.judge = _judge_class()()
        self.judge.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=recorded_create))
        )
        self.judge.model_name = model
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.__name__ = "sweqapro_judge"

    # 逐候选保存完成状态、可见证据和评分；grounded 模式遇到评分故障时阻止整组更新。
    def __call__(self, completions, task_id, question, reference, environments=None, **kwargs):
        from .rewards import shape_reward, visible_evidence

        records = []
        groups = defaultdict(list)
        for i, completion in enumerate(completions):
            final = completion[-1] if completion else {}
            text = (
                (final.get("content") or "")
                if final.get("role") == "assistant" and not final.get("tool_calls")
                else ""
            )
            answer = self.judge._extract_answer(text)
            self.active_evidence = visible_evidence(
                completion, answer, self.policy["max_evidence_characters"]
            )
            self.grounding = None
            started = time.monotonic()
            attempt_start = len(self.attempts)
            scores = (
                self.judge._llm_score(question[i], reference[i], answer)
                if answer and not self.collect_only
                else None
            )
            records.append(
                {
                    "task_id": task_id[i],
                    "answer": answer,
                    "scores": scores,
                    "missing_finish": answer is None,
                    "judge_failed": not self.collect_only and answer is not None and scores is None,
                    "review_pending": self.collect_only and answer is not None,
                    "judge_seconds": time.monotonic() - started,
                    "completion": completion,
                    "judge_requests": self.attempts[attempt_start:],
                    "evidence": self.active_evidence,
                    "grounding": self.grounding,
                    "source_identity": getattr(environments[i], "source_identity", None)
                    if environments
                    else None,
                    "tools": environments[i].events if environments else [],
                }
            )
            if scores is not None:
                groups[task_id[i]].append(scores)
        rewards = []
        with self.output.open("a", encoding="utf-8") as stream:
            for row in records:
                valid = groups[row["task_id"]]
                scores = row["scores"] or {
                    k: sum(s[k] for s in valid) / len(valid) if valid else 1.0 for k in WEIGHTS
                }
                audit = None
                if (
                    not self.collect_only
                    and not row["missing_finish"]
                    and not (row["judge_failed"] and self.policy["mode"] == "grounded")
                ):
                    audit = shape_reward(
                        scores, row["grounding"], row["evidence"], row["tools"], self.policy, WEIGHTS
                    )
                value = audit["reward"] if audit else 0.0
                row.update(
                    reward=None
                    if self.collect_only or (row["judge_failed"] and self.policy["mode"] == "grounded")
                    else value,
                    reward_imputed=not self.collect_only
                    and self.policy["mode"] == "quality"
                    and row["judge_failed"],
                    group_update_eligible=not self.collect_only
                    and not self.budget_exhausted
                    and not (self.policy["mode"] == "grounded" and any(r["judge_failed"] for r in records)),
                    reward_audit=audit,
                    reward_policy=self.policy,
                )
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                rewards.append(value)
        if self.budget_exhausted:
            raise RuntimeError("GRPO stopped at judge budget; raw rollout and request reservations preserved")
        if (
            not self.collect_only
            and self.policy["mode"] == "grounded"
            and any(r["judge_failed"] for r in records)
        ):
            raise RuntimeError(
                "No usable GRPO quality scores for the complete grounded group; raw candidates saved, no policy update"
            )
        if (
            not self.collect_only
            and any(r["judge_failed"] for r in records)
            and not any(r["scores"] for r in records)
        ):
            raise RuntimeError(
                "No usable GRPO quality scores; judge failures recorded before any policy update"
            )
        return rewards
