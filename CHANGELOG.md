# Changelog

All notable changes to RepoMentor are documented in this file.

## [0.3.2] - 2026-08-11

### Added

- A bounded Claude Agent Runtime that lets Explorer and Mentor choose repository investigations through RepoMentor's in-process MCP tools without receiving raw filesystem, shell, network, or editing access.
- Adaptive runtime routing that keeps complete small repositories and OpenAI-compatible providers on the deterministic Workflow while using bounded Agent discovery for larger Anthropic-compatible analyses.
- A trusted local Claude Agent SDK plugin manifest, namespaced Skills, programmatic Mentor specialist definitions, and opt-in evaluation assets for comparing Workflow and Agent modes.
- Structured Agent metrics and public Harness traces for runtime selection, tool summaries, turns, token usage, cost, and explicit fallback reasons.

### Changed

- Pinned `@anthropic-ai/claude-agent-sdk` to `0.3.224` and isolated every Agent query in a temporary working directory with project and target-repository settings disabled.
- Agent discovery now produces only an EvidencePlan; the Harness remains the sole source reader and performs one validated, budgeted evidence batch outside the Agent loop.
- Explorer, Mentor, and Contributor synthesis prefer native structured output and retain Zod validation, with Draft-07 schemas for Claude Code compatibility and text-JSON fallback for unsupported endpoints.
- Analysis caches are isolated by actual runtime kind under pipeline version `bounded-agent-v8`.
- Development watch mode preserves previous terminal output across backend restarts.
- Analysis traces use accurate completed-discovery counts and intentional empty-result wording instead of misleading zero-value summaries.
- Module Map importance tags now use coordinated low-saturation colors for `core`, `support`, and `utility`.

### Fixed

- Allowed the SDK-internal `StructuredOutput` terminal submission for main Agents without broadening repository permissions or allowing subagents to invoke it.
- Prevented Zod's Draft 2020-12 meta-schema from crashing Claude Code's `--json-schema` validator and classified local schema failures as non-retryable.
- Related-test discovery now associates directory-owned entry points such as `bin/nanoid.js` with tests such as `test/bin.test.js`.
- Mentor Agent completion traces now use the correct phase name instead of always identifying the Explorer.

## [0.3.1] - 2026-08-07

### Added

- An adaptive small-repository path that directly covers all relevant files when they fit the active Harness policy, avoiding redundant planning calls.
- A path-keyed Evidence Ledger with explicit `available`, `partial`, and `missing` states shared across analysis stages.
- Structured coverage gaps with stable kind, subject, summary, and severity fields.
- A unified bounded content-preparation layer for plain text and isolated structured-container adapters.

### Changed

- Evidence budgets are allocated after candidate contents are prepared, so request order no longer causes unnecessary truncation when the complete set fits.
- Mentor reuses a complete Explorer evidence set instead of repeating planning and file reads for small repositories.
- Contributor context prioritizes the strongest evidence referenced across Explorer and Mentor while remaining within a fixed context budget.
- The default analysis flow no longer pauses for project-type and dependency-graph interaction questions.
- Analysis Trace and runtime-boundary messages use concise Chinese descriptions and aggregate repeated operations.

### Fixed

- Later complete reads now resolve earlier evidence omissions, while partial reads continue to produce an explicit coverage limit.
- Shorter or less complete rereads can no longer overwrite stronger evidence already held by the Harness.
- Legitimate semantic gaps outside the read-attempt ledger are preserved instead of being incorrectly filtered from the report.
- Runtime-confirmed missing or partial evidence remains visible even when the report reaches its eight-gap display limit.
- Generated container metadata no longer consumes the bounded source-evidence budget when a content adapter is available.

## [0.3.0] - 2026-08-07

### Added

- Local Model Settings UI and API with API keys stored only under the ignored `data/` directory.
- A provider-neutral model event interface with Anthropic-compatible and OpenAI-compatible adapters.
- OpenAI-compatible Chat Completions streaming and JSON-response fallback support.
- A provider-neutral Repository Harness with explicit repository-map, evidence-batch, and contributor-context tool contracts.
- A live Analysis Trace showing plans, tool actions, evidence coverage, schema-validated stage completions, and user decisions.
- Shared Harness state for repository coverage, evidence plans, examined and skipped paths, user focus, and global read budgets across all three stages.
- Git history inspection moved from Pipeline glue into the provider-neutral Repository Harness domain-tool surface.
- Bounded `search_symbols`, `trace_module_dependencies`, and `find_related_tests` domain tools for repository understanding without reopening low-level Glob/Read loops.
- Executable project-type Skill packs: `SKILL.md` carries analysis strategy while validated `skill.json` manifests define tool permissions, evidence requirements, stop intent, and per-stage action/file limits enforced by the Harness.
- A single-command local launcher that builds missing production assets, starts the local service, opens the browser, and supports `--port`, `--host`, `--no-open`, and `--rebuild`.
- A package-level `repomentor` CLI entry with asset paths resolved independently from the user's local data directory.
- Clean backend builds that remove obsolete compiled modules before packaging, preventing deleted features from surviving as stale distribution files.

### Changed

- Model credentials are no longer required at process startup; analysis requests return a clear configuration error until a provider is configured.
- Generic `LLM_*` environment variables are supported while existing `ANTHROPIC_*` settings remain backward compatible.
- Analysis caches are isolated by provider endpoint and model so switching models cannot return a stale report from another provider.
- The default bind address is now `127.0.0.1` for local-first use.
- Repository access now runs through deterministic Harness operations shared by every model provider; public traces exclude source contents and hidden model reasoning.
- Evidence batches are now single-use stage transitions enforced by the Harness, with explicit total batch, file, and byte stop conditions.
- Orchestrator output now defines a goal, analysis questions, discovery actions, direct evidence requests, and visible stop intent; Harness observations and unresolved evidence are shared across stages.
- Patched production transitive dependencies for the `brace-expansion` and `fast-uri` high-severity advisories reported by npm audit.
- Model-setting writes are now restricted to loopback clients, preventing a remotely exposed local server from redirecting a saved API key to an attacker-controlled Provider endpoint.
- Each analysis task now freezes its Provider settings for planning, synthesis, retries, and JSON repair so reports and cache keys cannot mix configurations.
- Harness evidence allocation now reserves bounded slots for domain-tool discoveries and records every candidate omitted from the final batch.
- Verified evidence references now carry forward across stages, allowing Mentor to reuse Explorer-supported repository-profile evidence without broadly trusting unread files.

## [0.2.2] - 2026-07-30

### Changed

- Explorer now uses a smaller turn budget and reports aggregated tool-call progress instead of one log entry per SDK message.
- Invalid Agent JSON receives one short, tool-free repair pass rather than rerunning the complete repository scan.
- Detailed logs now display backend event timestamps captured when each event occurred.

### Fixed

- Explorer output now documents and enforces the exact `core`, `support`, and `utility` importance values.
- The unambiguous `supporting` alias is normalized to `support` before Schema validation.

## [0.2.1] - 2026-07-30

### Fixed

- Example buttons now populate complete GitHub URLs and use the correct `anthropics/anthropic-sdk-python` repository.
- Missing or inaccessible GitHub repositories now produce a clear preflight error instead of a generic Clone failure.

## [0.2.0] - 2026-07-30

### Added

- Persistent analysis task history with restart recovery.
- SSE event replay, reconnect support, and completed-task snapshot hydration.
- Reusable repository analysis experiences for later Mentor runs.
- Custom interaction answers that influence downstream agents.
- Production migration copying and cross-platform startup scripts.

### Changed

- Repository shorthand such as `owner/repo` is now accepted.
- Requested branches are cloned and refreshed independently.
- Runtime schemas now enforce documented importance and difficulty values.
- Production and development dependencies were upgraded to audited versions.

### Fixed

- Cached or fast-running tasks no longer lose completion events.
- Clone, stage, and total-task timeout settings are now enforced.
- Repository size limits and Agent file-access guards are now active.
- Fresh production databases now receive all required migrations.

## [0.1.0] - 2026-06-09

- Initial public release with the Explorer, Mentor, and Contributor pipeline.
- Fastify API, React dashboard, SSE progress, SQLite result cache, and repository analysis Skills.
