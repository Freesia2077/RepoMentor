# RepoMentor development scope

- Active development targets `python/`. Preserve the original TypeScript application in `src/`, `web/`, and its root scripts. Do not modify or run the TS application/tests unless the user explicitly requests it.
- Python is an independent package: its normal tests, runtime assets and CLI must not require Node, npm, the TS server, or the frontend. Historical parity checks are opt-in.
- On the owner's Windows machine, use `D:\anaconda\envs\repomentor\python.exe` with `-s` for every Python operation (`python/run.ps1` wraps it). Do not create another environment. On other machines use the explicitly configured project environment.
- Default to offline pytest, the stub runtime, and recorded result verification. Reuse saved reports; real model calls need a concrete validation purpose and should be kept to a minimum.
- Never commit credentials, model settings, runtime databases, repository caches, weights, or unrelated local documents.
- Distinguish workflow/schema success from answer correctness. Preserve original model failures and dataset split boundaries; do not claim human approval for model-reviewed seed tasks.
- RepoMentor and the planned MiniMind/Qwen comparison share the repository-learning assistant task, data protocol, tools and evaluation. New model experiments belong to the Python workflow.
