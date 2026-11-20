# Subagent reliability implementation plan

Goal: recover interrupted tasks, stop individual tasks, register any work directory, preserve role identity, explain failures, and configure Claude models from the active API endpoint.

Architecture: focused stdlib modules shared by runners, CLI and panel. Keep Kiro model catalog unchanged. No role snapshots or automatic task scheduling.

## Execution

- [x] Task lifecycle and registry: add isolated tests for live/dead/unknown process identity, PID reuse, idempotent stopping, registration concurrency and process group timeout; observe red, implement common module, integrate both runners and CLI.
- [x] Claude catalog and defaults: test settings/environment resolution with synthetic credentials, mock HTTP transport for pagination/auth errors/redirects, saved default precedence; observe red, implement shared resolver and catalog; never expose secrets.
- [x] Role metadata: test dispatched state identity and resumed role policy; observe red, add identity-only metadata and use latest role on continuation, refuse provider changes.
- [x] Panel integration: wire task recovery/stop, registered sources, role labels, defaults and model catalogs; test DOM with existing jsdom harness and endpoint contracts.
- [x] Diagnostics: test redaction/classification; retain bounded safe probe error, expose copyable task diagnostics and actionable permission error; never broaden permissions automatically.
- [x] Update Skill/documentation to shared task commands and latest config semantics; run full backend and DOM suite, review integration and fix regressions.

Validation commands: `/private/tmp/coder-agents-test-venv/bin/python -m unittest discover -s tests`, `npm test`. Tests bind localhost ephemeral ports and may need normal sandbox escalation. Do not run live paid model requests or clean user history. Preserve unrelated uncommitted changes; no automatic commits.

Verified: 182 backend tests and 38 DOM checks passed. The current Claude API directory refreshed successfully in the panel with 20 models, including wildcard and namespaced IDs; no live inference was triggered by this verification.
