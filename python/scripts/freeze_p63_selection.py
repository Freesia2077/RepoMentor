"""Record a development-only selection before opening confirmation outputs."""

from p63 import load, settings
from p63_dataset import OUT, ROOT
from record_p63_reviews import assessment, ref, verdict
from record_p63_v2_reviews import rerank_reviews
from run_rag_quality import frozen, sha

from repomentor.experiment_review import import_reviews, summarize_batch
from repomentor.sdk_agent import sdk_version


def main():
    expert = OUT / "runs/experts-v1"
    reviewed = rerank_reviews()
    keys = ["history-dev-01", "history-dev-04", "sweqa-flask-003", "sweqa-requests-001"]
    judgments = {k + "--sdk/answer": reviewed[k + "--sdk/answer"] for k in keys}
    judgments["sweqa-flask-003--sdk/answer"] = verdict("unverifiable", "not_applicable", "insufficient",
        "工具可用但未委派，也未补查蓝图缓存实现，检索失败拒答不算解决。",
        assessment("未回答蓝图静态资源缓存机制", "unverifiable", "源码存在 get_send_file_max_age 等路径。", ref("src/flask/blueprints.py", 55, 102)),
        abstention="inappropriate")
    frozen(expert / "review-decisions.json", judgments)
    import_reviews(expert, ROOT)
    summarize_batch(expert, ROOT)
    batches = ["development-v2-bm25", "development-v2-hybrid", "development-v2-hybrid-rerank", "experts-v1"]
    summaries = {b: load(OUT / "runs" / b / "comparison.json")["modes"] for b in batches}
    current = sdk_version()
    assert current == load(OUT / "runs/development-v2-hybrid-rerank/manifest.json")["sdk"]
    choice = {"version": "p63-selection-v1", "sdk": current, "profile": settings().public(),
        "retriever": "hybrid-rerank", "device": "cuda", "collaboration": "off",
        "basis": "Development semantic quality first: SDK solved 2/12 with hybrid-rerank, 0/12 with bm25 or hybrid. Fixed solved 1/12 in each. Small selected diagnostic set; no guarantee of confirmation improvement.",
        "experts": "Four development cases exposed specialist tools but produced zero delegations; no evidence for collaboration benefit. Keep off.",
        "summaries": summaries,
        "review_sha256": {b: sha(OUT / "runs" / b / "judgments.json") for b in batches},
        "confirmation_tasks_sha256": sha(OUT / "confirmation.json"),
        "selection_before_confirmation": True, "further_tuning": False,
        "training_eligible": False, "human_approved": False}
    if not (OUT / "selection.json").exists():
        assert not (OUT / "runs/confirmation-v1").exists(), "selection must precede confirmation"
    frozen(OUT / "selection.json", choice)
    print("Frozen hybrid-rerank / no experts / 8K / final 1536; confirmation has not informed selection.")


if __name__ == "__main__":
    main()
