"""Admit six ordered SWE-QA development items; never import held-out answers."""

import hashlib
import json
import urllib.request
from pathlib import Path

from repomentor.paths import data_root
from repomentor.records import write
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-d-external"
REVISION = "c13deac7a0d99b0ca2e593e004c4739475785b08"
REPOSITORIES = {
    "pallets/flask": ("flask", "85c5d93cbd049c4bd0679c36fd1ddcae8c37b642"),
    "psf/requests": ("requests", "46e939b5525d9c72b677340985582b04b128478a"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def download(url, name):
    path = data_root() / "external-qa" / REVISION / name
    if not path.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "RepoMentor-evaluation"})
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(4_000_001)
        if len(content) > 4_000_000:
            raise ValueError("external metadata/data file too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return path.read_bytes()


def freeze(path, value):
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError(f"frozen record differs: {path}")
    else:
        write(path, value)


def main():
    base = f"https://raw.githubusercontent.com/peng-weihan/SWE-QA-Bench/{REVISION}"
    corpus_file = ROOT / "experiments/p0/corpus.json"
    corpus = json.loads(corpus_file.read_text(encoding="utf-8"))
    existing = {r["repository"].casefold() for r in corpus["repositories"]}
    license_bytes = download(base + "/LICENSE", "LICENSE")
    commits = download(base + "/repo_commit.txt", "repo_commit.txt").decode()
    assert b"Apache License" in license_bytes
    known_questions = set()
    for line in (ROOT / "experiments/p4/tasks.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        assert row["split"] == "validation"
        known_questions.add(" ".join(row["input"]["question"].casefold().split()))
    store = SnapshotStore(data_root() / "corpus")
    tasks, references, manifests = [], {}, []
    for repository, (name, commit) in REPOSITORIES.items():
        assert repository.casefold() not in existing
        listed = next(line.split()[1] for line in commits.splitlines() if line.split()[0].endswith("/" + repository))
        assert commit.startswith(listed)
        meta = json.loads(download(f"https://api.github.com/repos/{repository}", name + "-metadata.json"))
        family = meta.get("parent", {}).get("full_name", meta["full_name"])
        assert family.casefold() not in existing and meta["full_name"].casefold() == repository.casefold()
        reader = store.import_repository(repository, commit)
        license_paths = [p for p in reader.paths if p.upper() in ("LICENSE", "LICENSE.RST", "LICENSE.TXT")]
        licenses = []
        for path in license_paths:
            content, truncated = reader.read(path, 100_000)
            assert not truncated
            licenses.append({"path": path, "sha256": sha(content.encode()), "text": content})
        assert licenses
        data = download(base + f"/Benchmark/{name}.jsonl", name + ".jsonl")
        rows = [json.loads(line) for line in data.decode("utf-8-sig").splitlines() if line.strip()]
        for ordinal, row in enumerate(rows[:3], 1):
            question = row["question"]
            normalized = " ".join(question.casefold().split())
            assert normalized not in known_questions
            known_questions.add(normalized)
            task_id = f"sweqa-{name}-{ordinal:03d}"
            tasks.append({"task_id": task_id, "repository": repository, "snapshot_id": reader.snapshot.snapshot_id,
                          "commit_sha": commit, "split": "external-development", "input": {"question": question},
                          "source_row": ordinal, "question_sha256": sha(question.encode())})
            references[task_id] = {"reference_answer": row["answer"], "source_row": ordinal,
                                   "status": "dataset_reference_not_automatically_truth", "human_approved": False}
        manifests.append({"repository": repository, "family": family, "fork": meta["fork"], "commit_sha": commit,
            "snapshot_id": reader.snapshot.snapshot_id, "source_url": base + f"/Benchmark/{name}.jsonl",
            "source_sha256": sha(data), "available_rows": len(rows), "selected_rows": [1, 2, 3],
            "snapshot_license_files": licenses, "upstream_metadata_sha256": sha(json.dumps(meta, sort_keys=True).encode())})
        print(json.dumps({"admitted": repository, "snapshot_id": reader.snapshot.snapshot_id, "selected": 3}), flush=True)
    freeze(OUT / "tasks.json", tasks)
    freeze(OUT / "evaluation-only/references.json", references)
    freeze(OUT / "admission.json", {"dataset": "peng-weihan/SWE-QA-Bench", "revision": REVISION,
        "repositories": manifests, "selection": "first three rows per repository, before generation; six development tasks",
        "original_partition": "Benchmark project JSONL; no train/test field", "target_split": "external-development",
        "license": "Apache-2.0", "dataset_license_sha256": sha(license_bytes),
        "local_corpus_sha256": sha(corpus_file.read_bytes()), "training_eligible": False, "human_approved": False,
        "heldout_answers_accessed": False, "dataset_license_url": base + "/LICENSE",
        "deduplication": "Canonical repository and parent-family exclusion; normalized exact question checks against P4 validation. "
                         "No claim of semantic/code-clone or pretraining contamination freedom.",
        "future_exclusions": list(REPOSITORIES), "pro_overlap_repositories_not_selected": ["pydata/xarray", "sphinx-doc/sphinx", "sqlfluff/sqlfluff"]})
    license_path = OUT / "DATA_LICENSE.txt"
    if license_path.exists():
        assert license_path.read_bytes() == license_bytes
    else:
        license_path.write_bytes(license_bytes)


if __name__ == "__main__":
    main()
