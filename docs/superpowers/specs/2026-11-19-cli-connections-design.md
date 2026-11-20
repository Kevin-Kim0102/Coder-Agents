# CLI connections

Approved on 2026-11-19: manage CLI connections in the browser settings. PATH discovery is a candidate lookup only, performed when the user clicks Connect, never an implicit connection on page load.

## Behavior

- Each supported CLI has a path field, connection status, Connect/Reconnect, Disconnect, Set default, and a separate Test call action.
- Connect resolves the entered absolute path or a PATH candidate, checks executability and CLI identity/version, then checks login with Claude `auth status --json` or Kiro `whoami`. Checks have bounded timeouts, no stdin and no shell. Following the user’s approved update, explicit panel Connect may open the official login command in a visible terminal when logged out, wait up to 180 seconds and recheck authentication before saving. Connect never sends model prompts. Successful connections automatically read the provider catalog; a catalog failure retains the connection and offers retry. Page loads remain passive. Version output must identify the chosen product. Failed checks do not persist the new connection or overwrite a valid saved path.
- Persist successful connections and the default provider to `connections.json` next to the panel config (private mode 0600). Preserve the selected symlink path. Persist no tokens, login output or account details. Saved status means last check succeeded, not guaranteed current authentication. On reading settings, check file availability only. Restarting the panel does not execute any CLI.
- Only connected, currently executable providers appear as available in the composer. No connection means display a settings hint and block sends. Historical conversations remain readable; their provider is immutable. Default can be changed explicitly. Existing CLI-only calls retain PATH fallback when no saved connection exists, while saved paths are authoritative and never silently replaced.
- Pass an explicit saved CLI path to runners and Kiro metadata services. Changed/disconnected paths affect future runs; active runs retain their executable.
- Test call is an explicit bounded tool-free model request and reports success/failure separately. Show quota use before the button. Do not run on connection or page load.
- Installation guidance initially describes the missing CLI and login command; a dedicated installation guide can follow.

## Verification

Test success, wrong product, logged-out, missing path, timeout, private persistence, symlink preservation, no subprocess on startup/read, failed reconnect preservation, default/disconnect handling, path dispatch, authenticated API boundaries and DOM settings actions. Run existing backend/DOM/packaging regression suites.
