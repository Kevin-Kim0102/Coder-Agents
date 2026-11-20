# Claude CLI provider design

Approved direction: extend the existing local CLI/panel with a Claude Code provider, retain Kiro compatibility, and expose the unified command through the optional Codex skill.

## Requirements and acceptance

| Requirement | Behavior | Verification |
| --- | --- | --- |
| Execute Claude tasks | `run --provider claude` reads stdin and persists private per-run logs; no implicit permission bypass | Fake CLI process exercises stdin, command arguments, success, error and missing result |
| Resume conversations | Persist native session ID and use `--resume`; preserve cwd and provider | Two-turn runner and panel routing tests |
| Use existing panel | Select provider for new conversations; existing conversations keep their provider; legacy records mean Kiro | Backend and DOM tests |
| Report real outcomes | Require successful result event and zero exit code; permission denials fail; never retry automatically | Error, interruption and malformed stream tests |
| Run on macOS | Claude process lifecycle uses POSIX process groups; Linux-only cleanup remains unavailable for Claude runs | Termination test; real CLI smoke test |
| Delegate from Codex | Update optional skill template with explicit provider, logs, continuation and review instructions | CLI help and documented smoke command |

## Architecture

Add a lightweight provider registry shared by the CLI and panel. Keep Kiro runner and its ACP gate unchanged. Add an independent Claude runner using `claude -p --verbose --output-format stream-json`, preserving raw events separately and adapting text/tool/result events to the viewer's existing public event vocabulary. This avoids rewriting the existing Kiro protocol.

Provider metadata declares model selection and account usage capabilities. Claude exposes configurable CLI model names (initially sonnet/opus/haiku aliases, explicitly not an account entitlement catalog); CLI validates real availability when executing. Default Claude model is sonnet with medium effort. Failed selection is not silently replaced. Model effort can be omitted for models that do not support it. Claude account credits are unsupported; only reported task usage is displayed.

Keep the existing work/kiro-runs directory for compatibility. New state records include provider and schema version; missing provider means kiro. Native Claude session data stays owned by Claude. Store task, raw stream, normalized stream, stderr, final text and state in private run directories. Run directories are created exclusively and state updates are atomic.

Noninteractive permissions use dontAsk by default (preauthorized tools still apply); optional acceptEdits is explicit. Never add bypassPermissions or dangerously-skip-permissions. Report permission denials visibly. Cancellation forwards to the child process group and persists cancelled state. No automatic retries or history cleanup.

Panel prefers Claude when installed, unless default_provider explicitly overrides it; otherwise defaults to Kiro. The browser waits for server provider configuration before its first model query. A conversation's provider cannot change on resume. Only available providers may start new tasks. Existing Kiro model/usage services initialize lazily so Claude use never probes Kiro.

## Scope

Implement execution, continuation, panel integration, Codex template, and macOS foreground support. No MCP server, launchd installer, data relocation, automated deletion or claim of Claude account quota discovery.
