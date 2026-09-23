"""Freeze public sources before creating tasks. No repository code is executed."""

import hashlib
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from repomentor.repository import SnapshotStore, is_source, is_test

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "p0"
STORE = ROOT / "artifacts" / "corpus"
REPOSITORIES = [
    ("train", "Python", "python-attrs/attrs"),
    ("train", "Python", "pypa/packaging"),
    ("train", "Python", "python-jsonschema/jsonschema"),
    ("train", "Python", "marshmallow-code/marshmallow"),
    ("train", "Python", "mahmoud/boltons"),
    ("train", "Python", "dateutil/dateutil"),
    ("train", "JS/TS", "sindresorhus/p-limit"),
    ("train", "JS/TS", "sindresorhus/p-queue"),
    ("train", "JS/TS", "ljharb/qs"),
    ("train", "JS/TS", "jashkenas/underscore"),
    ("train", "JS/TS", "reduxjs/redux"),
    ("train", "JS/TS", "immerjs/immer"),
    ("validation", "Python", "more-itertools/more-itertools"),
    ("validation", "Python", "dbader/schedule"),
    ("validation", "JS/TS", "ai/nanoid"),
    ("validation", "JS/TS", "developit/mitt"),
    ("test", "Python", "python-trio/outcome"),
    ("test", "Python", "jmespath/jmespath.py"),
    ("test", "JS/TS", "paulmillr/noble-hashes"),
    ("test", "JS/TS", "colinhacks/zod"),
]


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "RepoMentor-research/0.1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def freeze(item):
    split, language, name = item
    prior = OUT / "frozen" / (name.replace("/", "--") + ".json")
    previous = json.loads(prior.read_text(encoding="utf-8")) if prior.exists() else {}
    # Reviewed manifests are authoritative; restoring caches must not erase annotations.
    for filename in ("corpus.json", "legacy-regression.json"):
        manifest = OUT / filename
        if manifest.exists():
            rows = json.loads(manifest.read_text(encoding="utf-8"))["repositories"]
            previous = next((row for row in rows if row["repository"] == name), previous)
    reader = SnapshotStore(STORE).import_repository(name, previous.get("commit_sha", "HEAD"))
    if previous:
        return previous
    meta = get_json("https://api.github.com/repos/" + name)
    licenses = [p for p in reader.paths if re.search(r"(^|/)(license|copying)(\.|$)", p, re.I)]
    license_records = []
    for p in licenses[:12]:
        raw = reader.raw(p)
        license_records.append({"path": p, "sha256": hashlib.sha256(raw).hexdigest()})
    entry = {
        "repository": name,
        "split": split,
        "language_group": language,
        "family": "sindresorhus-async" if name.startswith("sindresorhus/") else name,
        "commit_sha": reader.snapshot.commit_sha,
        "snapshot_id": reader.snapshot.snapshot_id,
        "default_branch": meta["default_branch"],
        "fork": meta["fork"],
        "parent": meta.get("parent", {}).get("full_name"),
        "license_spdx": (meta.get("license") or {}).get("spdx_id"),
        "license_files": license_records,
        "file_count": len(reader.paths),
        "readme_paths": [p for p in reader.paths if "/" not in p and p.lower().startswith("readme")],
        "test_paths": [p for p in reader.paths if is_test(p) and is_source(p)][:8],
        "source": "https://github.com/" + name,
        "freeze_time": previous.get("freeze_time", datetime.now(timezone.utc).isoformat()),
        "test_policy": "sealed until P10" if split == "test" else "development permitted",
    }
    prior.parent.mkdir(parents=True, exist_ok=True)
    prior.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name, entry["commit_sha"], entry["license_spdx"], flush=True)
    return entry


def deduplicate(entries):
    # 20-token normalized shingles, with >=80% containment and >=80 tokens required.
    # Only hashes cross split boundaries; no test task/answer content is exposed to developers.
    exact = {}
    shingle_index = {}
    docs = []
    exclusions = set()
    duplicate_pairs = []
    for repo in entries:
        reader = SnapshotStore(STORE).open(repo["snapshot_id"])
        for p in reader.paths:
            if not is_source(p) or re.search(r"(^|/)(vendor|fixtures|node_modules|dist|build)/", p):
                continue
            try:
                text, truncated = reader.read(p, 500_000)
            except Exception:
                continue
            if truncated:
                exclusions.add((repo["repository"], p, "dedup_scan_truncated"))
                continue
            tokens = re.findall(r"[A-Za-z_][\w]*|\d+|[^\w\s]", text)
            if len(tokens) < 80:
                continue
            normal = " ".join(tokens)
            digest = hashlib.sha256(normal.encode()).hexdigest()
            shingles = {
                hashlib.blake2b(" ".join(tokens[i : i + 20]).encode(), digest_size=8).digest()
                for i in range(len(tokens) - 19)
            }
            matches = {}
            for shingle in shingles:
                for old_id in shingle_index.get(shingle, []):
                    if docs[old_id]["split"] != repo["split"]:
                        matches[old_id] = matches.get(old_id, 0) + 1
            for old_id, count in matches.items():
                old = docs[old_id]
                if count / min(len(shingles), old["shingle_count"]) >= 0.8:
                    duplicate_pairs.append(
                        {
                            "left": [old["repo"], old["path"]],
                            "right": [repo["repository"], p],
                            "containment": count / min(len(shingles), old["shingle_count"]),
                        }
                    )
                    # Remove both sides from all downstream chunk/sample generation.
                    exclusions.add((old["repo"], old["path"], "cross_split_near_duplicate"))
                    exclusions.add((repo["repository"], p, "cross_split_near_duplicate"))
            doc_id = len(docs)
            docs.append(
                {
                    "repo": repo["repository"],
                    "path": p,
                    "split": repo["split"],
                    "sha256": digest,
                    "shingle_count": len(shingles),
                }
            )
            exact.setdefault(digest, []).append(doc_id)
            # Winnowing-independent exact inverted index; no probabilistic hash sampling.
            for shingle in shingles:
                shingle_index.setdefault(shingle, []).append(doc_id)
    save(
        "deduplication.json",
        {
            "method": "20-token shingles; >=80 tokens; >=0.8 containment; source only",
            "limitations": "Does not detect semantic clones or pretrained model contamination.",
            "scanned_files": len(docs),
            "near_duplicate_pairs": duplicate_pairs,
            "exclusions": [
                {"repository": r, "path": p, "reason": reason} for r, p, reason in sorted(exclusions)
            ],
        },
    )


def main():
    if "--legacy" in sys.argv:
        if (OUT / "legacy-regression.json").exists():
            for row in json.loads((OUT / "legacy-regression.json").read_text(encoding="utf-8"))[
                "repositories"
            ]:
                freeze(("legacy-regression", "legacy", row["repository"]))
            return
        old = json.loads((ROOT.parent / "eval/agentic-benchmark.json").read_text(encoding="utf-8"))
        frozen = []
        for row in old["repositories"]:
            record = freeze(("legacy-regression", "legacy", row["repository"]))
            reader = SnapshotStore(STORE).open(record["snapshot_id"])
            record["original_gold"] = row["goldEvidence"]
            record["gold_path_audit"] = {
                kind: [
                    {
                        "path": p,
                        "exists": p in reader.paths,
                        "directory": any(f.startswith(p.rstrip("/") + "/") for f in reader.paths),
                    }
                    for p in paths
                ]
                for kind, paths in row["goldEvidence"].items()
            }
            frozen.append(record)
        save("legacy-regression.json", {"version": "python-legacy-v1", "repositories": frozen})
        return
    if "--dedup" in sys.argv:
        deduplicate(json.loads((OUT / "corpus.json").read_text(encoding="utf-8"))["repositories"])
        return
    with ThreadPoolExecutor(max_workers=3) as pool:
        entries = list(pool.map(freeze, REPOSITORIES))
    save(
        "corpus.json",
        {
            "version": "repomentor-corpus-v1",
            "split_counts": {"train": 12, "validation": 4, "test": 4},
            "repositories": entries,
        },
    )
    if (OUT / "upstream-versions.json").exists():
        return
    versions = {
        "minimind": get_json("https://api.github.com/repos/jingyaogong/minimind/commits/HEAD")["sha"],
        "qwen3.5-4b": get_json("https://huggingface.co/api/models/Qwen/Qwen3.5-4B")["sha"],
        "weights_downloaded": False,
    }
    save("upstream-versions.json", versions)


if __name__ == "__main__":
    main()
