# CLI cards and configuration

The user requests Claude as the default, connected/unconnected compact CLI cards with provider icons, pause/start, and a per-provider configuration page.

- Overview groups by saved connection; paused providers remain connected. Each card opens configuration; its separate pause/start button changes availability without terminating existing tasks or discarding saved path/login information.
- Connection records retain the existing enabled field for connected/disconnected compatibility, plus paused=false by default. Paused providers cannot start tasks or become default; start validates the saved executable before clearing pause. The preferred default stays selected while paused, and sending is blocked until resumed or another provider is selected.
- Configuration includes connect/reconnect, disconnect, quota-labelled test, fetch models with visible model/effort list, executable path, provider-specific official installation guide and login instructions. Model snapshots are read passively on opening configuration; explicit fetch and connect refresh them. Failed requests retain a previous list with an error message.
- Overview defaults to Claude first. Current saved default will be explicitly set to Claude without logging either provider out. CLI dispatch without a saved default uses Claude.
- Preserve form drafts and asynchronous feedback when navigating between overview and detail; use textContent for CLI-provided values, accessible buttons and responsive cards.

Verification: persistence and task gates for pause/start/disconnect and invalid paths; provider-specific detail actions; model lists update for a non-default provider; grouping, icons, draft retention, keyboard entry/back navigation and existing regression suites.

Follow-up from user annotations: removed overview pause explanation, all-connected empty-state copy, and overview operation feedback. Detail pages retain operation feedback.

Further annotations: configuration hides the overview heading; its back button is enlarged. Return-to-conversation sits at top left, and Settings titles the navigation underneath. Hover/focus highlights the entire provider card; the configuration button covers the full card while pause/start remains an independent action above that hit area.

Configuration primary action now matches the overview state: Connect when disconnected, Pause when running, Start when paused. It dispatches pause/start without reconnecting or launching login/model discovery. The DOM test first failed on the previous Reconnect label, then passed for both transitions and synchronization with overview controls.

Verification: backend pause/start tests failed before implementation, then passed; full backend regression passed 116 tests. Added default dispatcher and paused PATH fallback tests for focused follow-up verification. All 30 DOM tests passed before the copy simplification. Official website SVG marks are embedded locally with no external image requests. Real saved default changed from Kiro to Claude and panel restarted; user subsequently disconnected Kiro through the UI, which is preserved.
