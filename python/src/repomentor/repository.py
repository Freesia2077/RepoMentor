"""Immutable Git-object snapshots: never run or check out target repository code."""

# 将仓库冻结为 Git 对象快照，并提供路径、文件大小和编码受限的只读访问。
# 检索、引用和评审都绑定固定提交，分支之后的更新不会改变已保存的源码。

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .contracts import ManifestEntry, RepositorySnapshot


class RepositoryError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def normalize_path(value: str) -> str:
    value = value.replace("\\", "/")
    value = re.sub(r"^\./+", "", value)
    return value


# 统一为仓库相对路径后拒绝绝对路径、父目录跳转、Git 内部路径和控制字符。
def safe_path(value: str) -> str:
    value = normalize_path(value)
    if not value or value.startswith("/") or any(c in value for c in (":", "\x00", "\n", "\r")):
        raise RepositoryError("not_tracked", "invalid repository-relative path")
    if any(part in ("", ".", "..", ".git") for part in value.split("/")):
        raise RepositoryError("not_tracked", "invalid repository-relative path")
    return value


# 禁用交互提示、用户 Git 配置和 hooks；错误事件只保留受控分类，避免泄漏远端文本。
def git(*args: str, timeout: float = 120) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "protocol.ext.allow=never", *args],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError("git_timeout", "Git operation timed out") from exc
    if result.returncode:
        # Never echo user URLs, credentials or arbitrary remote stderr into public events.
        raise RepositoryError("git_failed", f"Git operation failed (exit {result.returncode})")
    return result.stdout


def utf8_prefix(data: bytes, limit: int) -> str:
    return data[: max(0, limit)].decode("utf-8", errors="ignore")


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # 将 HEAD 先解析成固定提交，再从 Git 对象构建清单，后续读取不依赖脏工作树。
    def import_repository(self, source: str, revision: str = "HEAD") -> "SnapshotReader":
        if revision != "HEAD" and not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise RepositoryError(
                "invalid_revision", "use HEAD for initial freeze, or a full 40-character SHA"
            )
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source) and not Path(source).exists():
            source = "https://github.com/" + source.removesuffix(".git") + ".git"
        if source.startswith("https://"):
            if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", source):
                raise RepositoryError(
                    "invalid_source", "remote source must be a public GitHub HTTPS repository"
                )
        elif Path(source).is_dir():
            source = str(Path(source).resolve())
            # Resolve local HEAD now, including dirty working trees only via an explicit future mode.
            revision = git("-C", source, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
        else:
            raise RepositoryError("invalid_source", "expected a local Git repository or public GitHub URL")
        source_key = hashlib.sha256(source.encode()).hexdigest()[:24]
        bare = self.root / "objects" / source_key
        if not bare.exists():
            bare.mkdir(parents=True)
            git("init", "--bare", str(bare))
        present = False
        if revision != "HEAD":
            try:
                git("--git-dir", str(bare), "cat-file", "-e", revision + "^{commit}")
                present = True
            except RepositoryError:
                pass
        if not present:
            # Each fetch gets its own ref; concurrent imports must not race on FETCH_HEAD.
            ref = "refs/repomentor/" + uuid4().hex
            git(
                "--git-dir",
                str(bare),
                "fetch",
                "--no-tags",
                "--depth=1",
                "--",
                source,
                revision + ":" + ref,
                timeout=180,
            )
            sha = git("--git-dir", str(bare), "rev-parse", ref).decode().strip()
            if revision != "HEAD" and sha.lower() != revision.lower():
                raise RepositoryError("snapshot_changed", "fetched commit differs from requested SHA")
        else:
            sha = revision.lower()
        manifest = []
        for record in git("--git-dir", str(bare), "ls-tree", "-rlz", sha).split(b"\0"):
            if not record:
                continue
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size = meta.decode().split()
            if kind != "blob":  # submodules are never initialized
                continue
            path = raw_path.decode("utf-8", errors="strict")
            try:
                safe_path(path)
            except RepositoryError:
                continue
            manifest.append(ManifestEntry(path=path, oid=oid, size=int(size), mode=mode))
        manifest.sort(key=lambda item: item.path)
        snapshot_id = hashlib.sha256((source + "\0" + sha).encode()).hexdigest()[:24]
        snapshot = RepositorySnapshot(
            snapshot_id=snapshot_id, source=source, commit_sha=sha, manifest=manifest
        )
        folder = self.root / "snapshots" / snapshot_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "snapshot.json"
        if not path.exists():
            path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        else:
            if RepositorySnapshot.model_validate_json(path.read_text(encoding="utf-8")) != snapshot:
                raise RepositoryError("snapshot_changed", "frozen snapshot manifest mismatch")
        return SnapshotReader(snapshot, bare)

    def open(self, snapshot_id: str) -> "SnapshotReader":
        if not re.fullmatch(r"[0-9a-f]{24}", snapshot_id):
            raise RepositoryError("invalid_snapshot", "invalid snapshot id")
        snapshot = RepositorySnapshot.model_validate_json(
            (self.root / "snapshots" / snapshot_id / "snapshot.json").read_text(encoding="utf-8")
        )
        key = hashlib.sha256(snapshot.source.encode()).hexdigest()[:24]
        return SnapshotReader(snapshot, self.root / "objects" / key)


class SnapshotReader:
    def __init__(self, snapshot: RepositorySnapshot, bare: Path):
        self.snapshot = snapshot.model_copy(deep=True)
        self.bare = bare
        self.entries = {entry.path: entry for entry in snapshot.manifest}
        self._cache: dict[str, bytes] = {}

    @property
    def paths(self):
        return list(self.entries)

    # 只读取快照清单允许的 Git 对象，路径与对象类型共同约束源码访问。
    def raw(self, path: str) -> bytes:
        path = safe_path(path)
        entry = self.entries.get(path)
        if entry is None:
            raise RepositoryError("not_tracked", "path is absent from the frozen manifest")
        if entry.mode not in ("100644", "100755"):
            raise RepositoryError("unreadable", "symlinks and nonregular objects cannot be read")
        # Bounded before process allocation. Notebook output containers have a separate ceiling.
        maximum = 20_000_000 if path.endswith(".ipynb") else 2_000_000
        if entry.size > maximum:
            raise RepositoryError("oversized", "file exceeds safe scan size")
        if path not in self._cache:
            data = git("--git-dir", str(self.bare), "cat-file", "blob", entry.oid)
            digest = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if digest != entry.oid or len(data) != entry.size:
                raise RepositoryError("snapshot_changed", "Git blob does not match the frozen manifest")
            if len(data) <= 100_000 and sum(map(len, self._cache.values())) + len(data) <= 8_000_000:
                self._cache[path] = data
            return data
        return self._cache[path]

    # 按字节上限解码原文并显式返回截断状态，调用方据此决定能否完整引用。
    def read(self, path: str, limit: int = 24_000) -> tuple[str, bool]:
        data = self.raw(path)
        if path.endswith(".ipynb"):
            try:
                parsed = json.loads(data)
                cells = parsed["cells"]
                if not isinstance(cells, list):
                    raise ValueError("invalid cells")
                parts = []
                for i, cell in enumerate(cells):
                    if not isinstance(cell, dict) or cell.get("cell_type") not in ("code", "markdown"):
                        continue
                    src = cell.get("source", "")
                    src = "".join(s for s in src if isinstance(s, str)) if isinstance(src, list) else src
                    if isinstance(src, str) and src:
                        parts.append(f"## Cell {i + 1} ({cell['cell_type']})\n{src}")
                data = "\n\n".join(parts).encode()
            except (ValueError, KeyError, TypeError) as exc:
                raise RepositoryError("unreadable", "invalid notebook") from exc
        if b"\0" in data[:limit]:
            raise RepositoryError("unreadable", "binary file")
        text = utf8_prefix(data, limit)
        return text, len(text.encode()) < len(data)

    def history(self):
        # A depth-one snapshot intentionally exposes only its pinned commit, not invented frequency statistics.
        return {
            "commitSha": self.snapshot.commit_sha,
            "historyDepth": 1,
            "recentThemes": git(
                "--git-dir", str(self.bare), "show", "-s", "--format=%s", self.snapshot.commit_sha
            )
            .decode("utf-8", errors="replace")
            .splitlines(),
            "frequencyStatisticsAvailable": False,
        }


SOURCE_EXTENSIONS = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".swift",
    ".dart",
    ".ex",
    ".exs",
    ".vue",
    ".svelte",
}
MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "composer.json",
    "gemfile",
    "deno.json",
}


def is_source(path: str):
    return PurePosixPath(path).suffix.lower() in SOURCE_EXTENSIONS and not re.search(
        r"(^|/)(dist|build|coverage|vendor|node_modules)(/|$)", path, re.I
    )


def is_test(path: str):
    return bool(
        re.search(
            r"(^|/)(test|tests|spec|specs|__tests__)(/|$)|\.(test|spec)\.[^/]+$"
            r"|(^|/)(test_[^/]+\.py|[^/]+_test\.py|test\.(js|mjs|cjs|ts))$",
            path,
            re.I,
        )
    )


def build_profile(reader: SnapshotReader):
    paths = reader.paths
    entries = [
        p
        for p in paths
        if is_source(p)
        and PurePosixPath(p).stem
        in {"index", "main", "app", "server", "cli", "__init__", "__main__", "mod", "lib"}
    ][:20]
    tests = [p for p in paths if is_test(p) and is_source(p)][:20]
    docs = [p for p in paths if "/" not in p and p.lower().startswith("readme.")]
    manifests = [
        p
        for p in paths
        if PurePosixPath(p).name.lower() in MANIFEST_NAMES
        and len(PurePosixPath(p).parts) <= 4
        and not re.search(r"(^|/)(test|tests|fixtures|examples|docs|\.github)/", p, re.I)
    ][:8]
    guidance = [p for p in paths if re.search(r"(^|/)(contributing|development)(\.|$)", p, re.I)][:3]
    configs = [
        p
        for p in paths
        if re.search(
            r"(^|/)(tsconfig[^/]*\.json|.*\.config\.[^/]+|[Mm]akefile|Dockerfile|\.github/workflows/[^/]+)$",
            p,
        )
    ][:10]

    def excerpts(selected, per_file, total):
        result = []
        for p in selected:
            if total <= 0:
                break
            try:
                content, truncated = reader.read(p, min(per_file, total))
            except RepositoryError:
                continue
            result.append({"path": p, "content": content, "truncated": truncated})
            total -= len(content.encode())
        return result

    preferred = list(dict.fromkeys(entries + tests + docs[:1] + manifests + configs + guidance))
    index = list(preferred)
    byte_count = sum(len(p.encode()) + 1 for p in index)
    for p in paths:
        if p in preferred:
            continue
        if len(index) >= 2500 or byte_count + len(p.encode()) + 1 > 80_000:
            break
        index.append(p)
        byte_count += len(p.encode()) + 1
    index.sort()
    readme = excerpts(docs[:1], 10_000, 10_000)
    return {
        "fileCount": len(paths),
        "fileIndex": index,
        "fileIndexTruncated": len(index) < len(paths),
        "entryCandidates": entries,
        "testCandidates": tests,
        "readme": readme[0] if readme else None,
        "manifests": excerpts(manifests, 4000, 14_000),
        "configFiles": excerpts(configs, 3000, 10_000),
        "guidanceFiles": excerpts(guidance, 4000, 8000),
        "exampleManifests": [],
        "snapshotId": reader.snapshot.snapshot_id,
        "commitSha": reader.snapshot.commit_sha,
    }
