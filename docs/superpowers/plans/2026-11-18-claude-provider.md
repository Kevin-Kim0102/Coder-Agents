# Claude Provider Implementation Plan

> Execution: inline in the current session, using executing-plans and test-driven-development. The user approved implementation on 2026-11-18.

**Goal:** Allow Codex and the local panel to execute and resume Claude Code tasks on macOS while retaining Kiro support.

**Architecture:** Introduce a provider registry and a separate Claude runner. Adapt Claude raw events to the existing viewer vocabulary and persist provider identity per conversation.

**Tech Stack:** Python standard library, Claude Code CLI, existing HTML/JavaScript viewer, unittest and jsdom.

## Task 1: Runner and provider registry

- [x] Add tests in tests/test_claude.py using an executable fake Claude CLI emitting system/assistant/user/result JSON lines. Verify stdin, cwd, native session resume, raw/normalized logs, explicit permission mode, error results, missing results, malformed output and nonzero exits.
- [x] Run `python3 -m unittest discover -s tests -p test_claude.py -v` and observe failures for missing provider support.
- [x] Add scripts/agent_providers.py (provider lookup, executable discovery, model configuration) and scripts/run_claude.py (execution, event adapter, private records, cancellation). Add scripts/run_agent.py for explicit dispatch and update coder_agents/cli.py.
- [x] Run the targeted tests and resolve failures.

## Task 2: Panel and viewer

- [x] Add panel tests for automatic Claude default, legacy Kiro identity, immutable provider on continuation, model endpoint routing and no Kiro probes in Claude mode.
- [x] Run targeted tests before implementation.
- [x] Update scripts/watch_kiro.py to initialize Kiro services lazily, route sends by provider, return provider metadata, and summarize Claude usage. Preserve existing Kiro defaults and record behavior.
- [x] Update assets/viewer.html with provider selection, provider-aware model/usage fetches and continuation locking. Adapt tool/text output and avoid duplicate replay.
- [x] Extend tests/dom_review.cjs for Claude events and provider controls; run `npm test`.

## Task 3: Packaging, documentation and integration

- [x] Update runtime resource declarations, README, docs/architecture.md, docs/codex-integration.md and integrations/codex templates.
- [x] Run `python3 -m unittest discover -s tests -v`, `npm test`, `python3 -m coder_agents selfcheck` and check the diff.
- [x] Run a tool-free Claude smoke request and resume in a temporary workspace; verify saved session and actual result. If network or permission restrictions prevent it, report the exact limitation without substituting fixture results.
- [x] Report tested behavior and remaining platform limitations.

## Verification results

- Full Python suite: 91 passed using a temporary virtual environment with setuptools>=61 and wheel. Loopback HTTP tests ran outside the restricted sandbox.
- DOM suite: 21 passed; provider selection, immutable continuation provider, Claude text/tool rendering and no duplicate final response covered.
- Runtime selfcheck, git diff whitespace check and rendered Codex skill validation passed.
- Real Claude Code 2.1.78: tool-free request and native session resume both returned CLAUDE_READY; actual model claude-sonnet-4-6.
- Real isolated coding probe: Write and Read tool events captured, probe.txt contained CLAUDE_TOOL_OK, final reply TOOL_OK.
- SIGTERM, timeout, and a descendant ignoring SIGTERM are covered. A missing result, malformed stream, CLI nonzero exit, error result or permission denial fails the run.
- Personal Codex skill installation was not performed; the repository includes the validated template and installation instructions. No global instructions were changed.
- macOS foreground execution is verified. systemd installation and Kiro managed-temp cleanup retain Linux limitations. Claude account quota discovery is unsupported.
