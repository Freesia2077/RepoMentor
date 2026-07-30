# Changelog

All notable changes to RepoMentor are documented in this file.

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
