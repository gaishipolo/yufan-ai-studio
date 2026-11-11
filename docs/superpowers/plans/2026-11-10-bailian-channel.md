# Beijing Bailian Channel Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the isolated provider and UI tasks, and review the complete integration before delivery.

**Goal:** Let replacement-workbench users select LibTV or Beijing Bailian Wan 3.0 for each new task.

**Architecture:** Freeze `channel` in the existing configuration JSON, preserving old runs as LibTV without a database migration. Register a Bailian ModelProvider using the existing generation runner. Read frozen input snapshots, translate Mixed references to per-media-type references, upload local media using official temporary storage, persist upstream receipts, and download successful output to the existing archive. Never switch or resubmit an accepted task automatically.

**Tech Stack:** Python, httpx, FastAPI, SQLAlchemy, React, TypeScript, pytest, Vitest.

### Task 1: Official provider
- [x] Add `api/tests/test_bailian_provider.py` first: assert media numbering, upload contract, status normalization, receipt reuse, uncertain submit handling, and download.
- [x] Run `.venv/Scripts/python -m pytest tests/test_bailian_provider.py -q` from api; confirm the missing provider fails.
- [x] Implement `api/src/infra/ai/bailian.py` and focused helpers. Settings prefix `BAILIAN_`, default Beijing host `llm-cz0qm1j8cl2weo19.cn-beijing.maas.aliyuncs.com`, models `bailian-wan3-replacement` and `bailian-wan3-prime-replacement`.
- [x] Re-run provider tests and Ruff. Do not put API keys in tracked files.

### Task 2: Business integration
- [x] Add backend tests for default channel, invalid channel, frozen routing, accepted task recovery, and Bailian billing separation.
- [x] Add `channel` to prepare/run DTOs and catalog `channels` with enabled status. Store in configuration JSON and include it in idempotency fingerprint.
- [x] Resolve each run's model from the frozen channel. Pass raw upstream ID for Bailian and retain LibTV's existing run-id prefix. Keep authorization checks before uploads and submissions.
- [x] Route Bailian preparation independently of the browser executor; publish downloaded outputs through the existing archive.
- [x] Check media count, duration, size, frame rate and dimensions before charged submission; preserve task IDs and uncertain-submission guards.
- [x] Run affected replacement/recovery tests.

### Task 3: User interface
- [x] Add UI test: choose Bailian, prepare request contains channel, review shows paid API notice without LibTV acceleration checkbox.
- [x] Add channel selector to single/batch configuration and include choice in stable request key. Show frozen channel in review/history/detail and retain it when retrying.
- [x] Update TypeScript API types, mocked catalog, and channel-specific submit feedback. Support unavailable channel hints.
- [x] Run affected Vitest tests, TypeScript, ESLint and build.

### Task 4: Configuration and verification
- [x] Add documented BAILIAN settings to `.env.example`; configure supplied key only in ignored local `.env`, enable provider while retaining existing providers.
- [x] Perform non-generating authenticated API probe against Beijing; do not create a billable generation merely for validation.
- [x] Regenerate API artifacts using the repository script; run targeted regressions plus relevant config guards.
- [x] Review final diff for secret leakage, model routing, paid copy, uncertainty semantics, archive visibility, and existing uncommitted work preservation.

User approved the manual channel selection design and provided the Beijing workspace in chat on 2026-11-10. Existing working-tree changes are retained; no bulk staging or resetting is permitted.

2026-11-10 完成状态：四个任务全部落地。后端 1215 passed / 12 skipped，Ruff 全通过；
前端 529 passed，tsc -b、eslint --max-warnings 0、vite build 全通过。
北京节点已用 `GET /uploads?action=getPolicy` 做免计费鉴权探测（HTTP 200，字段与
`bailian.py` 预期一致），未创建任何计费任务。运行中的 8080 后端进程早于本次改动，
需重启后才在界面上出现渠道下拉。

## Verification result

163 targeted backend tests and 68 frontend tests passed. Full backend Ruff and mypy (265 files), frontend TypeScript/ESLint/production build, and generated API artifact checks passed. Live Beijing model and both upload-policy reads returned HTTP 200. The running local catalog exposes all three enabled channels. No billable generation was created.
