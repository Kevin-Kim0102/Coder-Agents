# CLI Connections Implementation Plan

**Goal:** Explicitly connect local CLIs from panel settings and persist the selected executable.

**Architecture:** Add a focused connection store/probe module. Panel settings endpoints share existing cookie, same-origin and JSON checks. Runners receive saved executable paths; ordinary page loads never probe login or launch Kiro.

**Tech stack:** Python standard library, existing HTML/JS, unittest/jsdom.

- [x] Write connection tests using a fake executable for version/auth/missing/wrong-product/timeout behavior; run red.
- [x] Implement `scripts/cli_connections.py`: private atomic save, executable validation, connection/auth checks, defaults and disconnect.
- [x] Test and implement panel endpoints and provider availability gates. Integrate saved paths with both runners and Kiro metadata.
- [x] Write DOM tests for first-run unconnected state, Connect success/failure, saved path/default/disconnect and explicit Test call; run red.
- [x] Add settings controls; preserve typed paths across polling; keep model requests behind connection availability.
- [x] Run focused/full suites, resource selfcheck, template verification and a real auth-only connection probe in a temporary store. Restart the local foreground panel only after tests pass.
- [x] Update README and connection behavior documentation with current limitations.

## Validation

- Python suite: 107 passed. DOM suite: 24 passed. Resource selfcheck and git diff whitespace check passed.
- Real Claude connection probe passed without a model request.
- Real Kiro CLI 3.0.0 connection and tool-free model call passed on macOS; response KIRO_READY, claude-sonnet-5.5 / medium, exit 0. Fixed native macOS runtime/server discovery and added platform-path tests.
- Ordinary page reads remain passive for connected Kiro; directory and usage refresh are explicit actions.
- Local panel restarted with saved connections and default preserved.

## Approved follow-up: login and automatic catalogs

- Explicit Connect opens the official login command in a visible terminal only when authentication fails; checks again before saving, with a 180-second wait limit. Passive page reads never launch login.
- Connect success automatically fetches the provider catalog; failures preserve connection and offer retry. The browser consumes the returned catalog directly.
- Tests: observed missing automatic catalog read and browser catalog reset failures, then implemented the flow. Full Python suite: 113 passed; DOM suite: 25 passed.
- Real local Kiro connection returned 22 catalog entries with status ok, without a model request. Logged-out flow uses fake CLI authentication transitions and a mocked terminal launcher; the user’s real account was not logged out.
