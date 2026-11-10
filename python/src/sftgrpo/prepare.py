"""Fetch pinned public inputs, prepare source-disjoint splits and assistant-only labels."""

import argparse
import gzip
import hashlib
import io
import os
import re
import tarfile
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import (
    PROJECT_ROOT,
    ROOT,
    SOURCE_ROOT,
    data_path,
    digest,
    identity,
    load_json,
    normalize,
    question_key,
    read_rows,
    report_path,
    save_json,
    supervision_labels,
    trajectory_audit,
    write_rows,
)


def fetch():
    lock = load_json(ROOT / "sources.lock.json")
    for item in lock["files"]:
        target = ROOT / item["path"]
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
            continue
        print("fetch", item["path"], flush=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = urllib.request.urlopen(item["url"], timeout=180).read()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Hash mismatch: {item['path']}")
        target.write_bytes(data)
    with zipfile.ZipFile(ROOT / ".cache/sources" / lock["trl_wheel"]) as wheel:
        (ROOT / ".cache/training-template.jinja").write_bytes(wheel.read(lock["template"]))


def task(repo, commit, question, answer, system):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid repository identity")
    basename = repo.split("/")[-1].casefold()
    prompt = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"Repository Path: /repo\nQuestion: {question}\n\nAnalyze the codebase using the read-only tools. Explain your investigation, cross-check findings, and put the final answer inside <finish>...</finish>.",
        },
    ]
    return {
        "task_id": digest(repo.casefold() + commit + question_key(question))[:20],
        "repo": repo,
        "basename": basename,
        "commit": commit,
        "question": question,
        "reference": answer,
        "prompt": prompt,
    }


def ordered(values, seed):
    return sorted(values, key=lambda x: digest(str(seed) + str(x)))


def prepare():
    import json

    import pyarrow.parquet as pq
    from transformers import AutoTokenizer

    policy = load_json(ROOT / "data-policy.json")
    seed = policy["seed"]
    raw = read_rows(ROOT / "data/raw/sft.jsonl")
    system = raw[0]["messages"][0]["content"]
    if len({r["messages"][0]["content"] for r in raw}) != 1:
        raise ValueError("Review differing system prompts before preparation")
    save_json(ROOT / "tool-schema.json", raw[0]["tools"])
    data_path("system.txt").parent.mkdir(parents=True, exist_ok=True)
    data_path("system.txt").write_text(system, encoding="utf-8")
    bench = [
        task(r["repo"], r["commit_id"], r["question"], r["answer"], system)
        for r in read_rows(ROOT / "data/raw/bench.jsonl")
    ]
    official_rl = pq.read_table(ROOT / ".cache/upstream/train/RL/verl-tool/dataset/train.parquet").to_pylist()
    if len(official_rl) != 464:
        raise ValueError("Unexpected official RL row count")
    rl = []
    for row in official_rl:
        info, reward = row["extra_info"], row["reward_model"]
        if info["split"] != "train" or not reward["ground_truth"].strip():
            raise ValueError("Invalid RL training row")
        rl.append(
            task(info["repo_name"], info["commit_id"], reward["question"], reward["ground_truth"], system)
        )
    old_names = {r.split("/")[-1].casefold() for r in policy["exclude_training_repositories"]}
    bench_names = {t["basename"] for t in bench}
    reserved = old_names | bench_names
    unavailable = {(r["repo"], r["commit"]) for r in policy.get("unavailable_training_snapshots", [])}
    candidates = [
        r for r in rl if r["basename"] not in reserved and (r["repo"], r["commit"]) not in unavailable
    ]
    candidates.sort(key=lambda r: digest(str(seed) + r["task_id"]))
    rl_dev = candidates[: policy["rl_dev_tasks"]]
    rl_dev_names = {r["basename"] for r in rl_dev}
    sft_names = {identity(r)[0] for r in raw} - reserved - rl_dev_names
    sft_dev_names = set(ordered(sft_names, seed)[: policy["sft_dev_repositories"]])
    rl_train = [
        r for r in candidates[policy["rl_dev_tasks"] :] if r["basename"] not in sft_dev_names | rl_dev_names
    ][: policy["rl_train_tasks"]]
    if len(rl_train) != policy["rl_train_tasks"]:
        raise ValueError("Not enough RL tasks")
    eval_names = ordered(bench_names - old_names, seed)[: policy["eval_repositories"]]
    evaluation = []
    for name in eval_names:
        group = sorted(
            (r for r in bench if r["basename"] == name), key=lambda r: digest(str(seed) + r["task_id"])
        )
        evaluation += group[: policy["eval_questions_per_repository"]]
    sets = {"rl-train": rl_train, "rl-dev": rl_dev, "eval": evaluation}
    for name, rows in sets.items():
        write_rows(data_path(f"{name}.jsonl"), rows)

    tok = AutoTokenizer.from_pretrained(ROOT / ".cache/tokenizer", local_files_only=True)
    tok.chat_template = (ROOT / ".cache/training-template.jinja").read_text(encoding="utf-8")
    counts, lengths, admissions, membership = Counter(), [], Counter(), []
    sft_questions, sft_train_names = set(), set()
    with gzip.open(data_path("sft-tokens.jsonl.gz"), "wt", encoding="utf-8") as output:
        for i, record in enumerate(raw):
            row, audit = normalize(record)
            name, question = identity(record)
            batch = tok.apply_chat_template(
                row["messages"],
                tools=row["tools"],
                tokenize=True,
                return_dict=True,
                return_assistant_tokens_mask=True,
                add_generation_prompt=False,
                enable_thinking=False,
            )
            ids, mask = batch["input_ids"], batch["assistant_masks"]
            if not any(mask) or len(ids) != len(mask):
                raise ValueError(f"Invalid assistant mask at row {i}")
            lengths.append(len(ids))
            counts["restored_observations"] += audit["restored"]
            counts["tool_calls"] += audit["calls"]
            counts["sequence_tokens"] += len(ids)
            counts["assistant_target_tokens"] += sum(mask)
            reason = (
                "reserved_repository"
                if name in reserved
                else "rl_dev_repository"
                if name in rl_dev_names
                else "over_32k"
                if len(ids) > 32768
                else None
            )
            split = "excluded" if reason else "dev" if name in sft_dev_names else "train"
            counts[f"sft_{split}"] += 1
            if reason:
                counts[reason] += 1
            else:
                for limit in [16384, 24576, 32768]:
                    if len(ids) <= limit:
                        admissions[f"{split}_{limit}"] += 1
                labels = [token if take else -100 for token, take in zip(ids, mask, strict=True)]
                output.write(
                    json.dumps({"row_index": i, "split": split, "input_ids": ids, "labels": labels}) + "\n"
                )
                counts[f"{split}_input_tokens"] += len(ids)
                counts[f"{split}_all_assistant_tokens"] += sum(mask)
                counts[f"{split}_last_assistant_tokens"] += sum(
                    v != -100 for v in supervision_labels(labels, "last_assistant")
                )
                if split == "train":
                    sft_questions.add(question_key(question))
                    sft_train_names.add(name)
            membership.append(
                {
                    "row_index": i,
                    "source_basename": name,
                    "question_sha256": question_key(question),
                    "tokens": len(ids),
                    "target_tokens": sum(mask),
                    "split": split,
                    "reason": reason,
                    "behavior": trajectory_audit(row["messages"]),
                }
            )
            if i % 100 == 0:
                print("tokenized", i, flush=True)
    train_names = sft_train_names | {r["basename"] for r in rl_train}
    dev_names = sft_dev_names | rl_dev_names
    assert not train_names & dev_names and not train_names & bench_names and not train_names & old_names
    assert not sft_questions & {question_key(r["question"]) for r in evaluation + rl_dev}
    question_sets = {name: {question_key(r["question"]) for r in rows} for name, rows in sets.items()}
    assert all(
        not question_sets[a] & question_sets[b]
        for a, b in [("rl-train", "rl-dev"), ("rl-train", "eval"), ("rl-dev", "eval")]
    )
    report = {
        "status": "cpu_preparation_only",
        "seed": seed,
        "official_rows": {"sft": len(raw), "rl": len(rl), "bench": len(bench)},
        "counts": dict(counts),
        "full_trajectory_admissions": dict(admissions),
        "training_template_sha256": digest(tok.chat_template),
        "token_length": {
            "min": min(lengths),
            "median": sorted(lengths)[len(lengths) // 2],
            "max": max(lengths),
        },
        "splits": {
            name: {"tasks": len(rows), "repositories": len({r["repo"] for r in rows})}
            for name, rows in sets.items()
        },
        "eval_repository_names": sorted({r["repo"] for r in evaluation}),
        "exclusions": {
            "previous_rag_repositories": len(old_names),
            "all_bench_repositories": len(bench_names),
            "unavailable_rl_training_snapshots": len(unavailable),
        },
        "overlap_checks": {
            "training_vs_dev_sources": 0,
            "training_vs_bench_sources": 0,
            "training_vs_old_rag_sources": 0,
        },
        "limitations": [
            "SFT provenance identifies a repository basename, not a verified URL or commit; collisions are excluded conservatively.",
            "All retained traces are intact; semantic teacher correctness and pretraining contamination remain unverified.",
            "32K GPU fit, throughput and model quality are not measured. No training or paid calls performed.",
        ],
    }
    report["data_policy_sha256"] = hashlib.sha256((ROOT / "data-policy.json").read_bytes()).hexdigest()
    report["prepared_files"] = {
        name: hashlib.sha256(data_path(name).read_bytes()).hexdigest()
        for name in ["sft-tokens.jsonl.gz", "system.txt", "rl-train.jsonl", "rl-dev.jsonl", "eval.jsonl"]
    }
    report["behavior_review"] = {
        "policy": "flag_and_review; retain identical members in both supervision conditions",
        "train_duplicate_trajectories": sum(
            m["split"] == "train" and m["behavior"]["duplicate_requests"] > 0 for m in membership
        ),
        "train_external_finalization": sum(
            m["split"] == "train" and m["behavior"]["external_finalization"] for m in membership
        ),
    }
    save_json(report_path("preparation.json"), report)
    save_json(report_path("sft-membership.json"), membership)
    save_json(
        report_path("eval-selection.json"),
        [{k: r[k] for k in ["task_id", "repo", "commit"]} for r in evaluation],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def snapshots():
    """Materialize only chosen online tasks; fixed commits, no target installation/execution."""
    tasks = [r for name in ["rl-train", "rl-dev", "eval"] for r in read_rows(data_path(f"{name}.jsonl"))]
    unique = {(r["repo"], r["commit"]) for r in tasks}

    def get(entry):
        repo, commit = entry
        dest = ROOT / ".cache/repos" / (repo.replace("/", "--") + "--" + commit)
        marker = dest / ".snapshot.json"
        archive_path = ROOT / ".cache/archives" / (dest.name + ".tar.gz")
        if (
            marker.exists()
            and archive_path.exists()
            and (os.name == "nt" or load_json(marker).get("materialized_complete"))
        ):
            return load_json(marker)
        print("snapshot", repo, flush=True)
        if archive_path.exists():
            content = archive_path.read_bytes()
        else:
            content = urllib.request.urlopen(
                f"https://codeload.github.com/{repo}/tar.gz/{commit}", timeout=180
            ).read()
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(content)
        archive_hash = hashlib.sha256(content).hexdigest()
        if marker.exists() and load_json(marker)["archive_sha256"] != archive_hash:
            raise ValueError(f"Changed source archive: {repo}@{commit}")
        files = 0
        folded, collisions = {}, []
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            for member in archive:
                parts = Path(member.name).parts[1:]
                if not parts or not member.isfile():  # Ignore links, submodules and special files.
                    continue
                target = (dest / Path(*parts)).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise ValueError("Archive path escapes snapshot")
                relative = "/".join(parts)
                if relative.casefold() in folded and folded[relative.casefold()] != relative:
                    collisions.append([folded[relative.casefold()], relative])
                folded[relative.casefold()] = relative
                if os.name == "nt":
                    target = Path("\\\\?\\" + str(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream:
                    target.write_bytes(stream.read())
                files += 1
        result = {
            "repo": repo,
            "commit": commit,
            "archive_sha256": archive_hash,
            "files": files,
            "windows_case_collisions": collisions,
            "materialized_complete": not (os.name == "nt" and collisions),
        }
        save_json(marker, result)
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(get, sorted(unique)))
    save_json(
        report_path("snapshots.json"),
        [{k: v for k, v in r.items() if k != "materialized_complete"} for r in records],
    )
    print("Prepared fixed snapshots:", len(records))


def bundle():
    """Build a portable allowlisted handoff, excluding credentials, Windows wheels and partial checkouts."""
    paths = set(SOURCE_ROOT.glob("*.py")) | set(ROOT.glob("requirements*.txt"))
    paths |= {
        ROOT / name
        for name in [
            "run-config.json",
            "data-policy.json",
            "sources.lock.json",
            "tool-schema.json",
            "README.md",
            ".gitignore",
        ]
    }
    paths |= {ROOT / r["path"] for r in load_json(ROOT / "sources.lock.json")["files"]}
    paths |= set(report_path("").glob("*.json")) | set((ROOT / "tests").glob("*.py"))
    paths |= set((ROOT / ".cache/archives").glob("*.tar.gz"))
    paths |= (
        set(data_path("").glob("*.jsonl"))
        | set(data_path("").glob("*.gz"))
        | {data_path("system.txt"), ROOT / ".cache/training-template.jinja"}
    )
    paths -= {report_path("bundle-manifest.json"), report_path("rent-ready.json")}
    manifest = [
        {
            "path": p.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
        }
        for p in sorted(paths)
    ]
    save_json(report_path("bundle-manifest.json"), manifest)
    target = ROOT / ".cache/rent-ready.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for record in manifest:
            archive.add(PROJECT_ROOT / record["path"], arcname=record["path"])
        archive.add(
            report_path("bundle-manifest.json"),
            arcname=report_path("bundle-manifest.json").relative_to(PROJECT_ROOT).as_posix(),
        )
    result = {
        "archive": ".cache/rent-ready.tar.gz",
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "files": len(manifest) + 1,
        "status": "CPU preparation verified; Linux tool isolation and real GPU training still require cloud preflight",
    }
    save_json(report_path("rent-ready.json"), result)
    print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fetch", "data", "snapshots", "bundle"])
    args = parser.parse_args()
    {"fetch": fetch, "data": prepare, "snapshots": snapshots, "bundle": bundle}[args.action]()
