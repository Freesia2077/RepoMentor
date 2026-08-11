# Agentic benchmark

This benchmark is deliberately opt-in because it makes paid live-model calls. During the first baseline, resolve and write the 40-character default-branch commit SHA for all eight repositories in `agentic-benchmark.json`; subsequent Workflow, bounded Explorer Agent, and Mentor multi-agent runs in that series must use those exact SHAs.

Store one row per repository and mode in `eval/results.json` using these fields: `repository`, `commitSha`, `mode`, `success`, `schemaValid`, `goldRecall`, `unsupportedClaims`, `legalCitationRate`, `evidenceBatches`, `filesRead`, `evidenceBytes`, `actionsWithinSkillBudget`, `tokenCost`, and `latencyMs`.

Run the gates with:

```bash
RUN_AGENT_EVAL=1 npm run eval:agentic
```

Mentor subagents stay experimental unless every gate passes. A failed benchmark does not disable the default bounded Explorer Agent.
