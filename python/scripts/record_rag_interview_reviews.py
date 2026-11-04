"""Serialize explicit source-read Codex assessments; this does not infer semantic scores."""

from rag_interview import OUT, freeze, load


def main():
    packet = {r["candidate_id"]: r for r in load(OUT / "review/packet.json")}
    manual = load(OUT / "review/manual-assessments.json")
    rows = []
    for values in manual["assessments"]:
        cid, correct, support, complete, note, *options = values
        candidate = packet[cid]
        abstention = options[0] if options else "not_applicable"
        consistency = options[1] if len(options) > 1 else "consistent"
        status = {"correct": "supported", "partially_correct": "partial", "incorrect": "contradicted",
                  "unverifiable": "unverifiable"}[correct]
        evidence = [{k: s[k] for k in ("path", "start_line", "end_line")}
                    for s in candidate["evaluation_sources"]] if status != "unverifiable" else []
        rows.append({"candidate_id": cid, "correctness": correct, "citation_support": support,
            "completeness": complete, "abstention": abstention,
            "entry_scope": "unverifiable" if correct == "unverifiable" else "correct",
            "consistency": consistency, "notes": note,
            "claims": [{"claim": "对当前问题的主要行为结论及必要机制，具体错误或支持见说明",
                        "status": status, "rationale": note, "evidence": evidence}]})
    if len(rows) != len(packet) or {r["candidate_id"] for r in rows} != set(packet):
        raise ValueError("manual reviews must cover every actual candidate exactly once")
    freeze(OUT / "review/decisions.json", rows)


if __name__ == "__main__":
    main()
