# Settings navigation

User approved a sidebar with separate content pages, with CLI installation guidance kept under CLI connections.

- CLI connections is the first page: provider cards, connection status/actions, advanced executable path, and installation/login guidance.
- Storage and cleanup contains temporary files, scoped cleanup, history retention and log location.
- Service and resources contains memory, idle exit, service cleanup status and a separated shutdown action.
- Help contains common questions about connection vs model testing, provider selection and historical conversations. Installation stays in CLI connections.
- Desktop uses a left navigation and an independently scrolling content pane. Narrow screens use a wrapping top navigation without horizontal overflow.
- Tabs support keyboard arrows, Home/End and visible focus. Hidden panels are excluded from focus. Switching preserves form input and in-flight connection state; reopening settings remembers the current category for the page session.
- Page reads remain passive. Temporary overview is fetched only when its storage page is shown. Existing cleanup dialogs, Escape/back behavior and backend APIs remain compatible.

Implementation: add DOM tests for categorization, keyboard navigation, retained form state and lazy storage reads; observe failure; update the existing HTML/CSS/JS; run DOM suite and resource checks, then verify desktop/mobile appearance in the browser.

Validation: new navigation tests failed before implementation. All 28 DOM tests passed after implementation, including keyboard navigation, session category memory, path preservation and lazy storage loading. Resource selfcheck and whitespace checks passed. Desktop sidebar and narrow top navigation were visually verified in the running panel; no horizontal document overflow was observed. Existing backend APIs were unchanged. Installation/login guidance and official install links are inside the CLI page; common questions are on Help.
