# Codex weekly usage widget

Build a native GTK3 popup modeled on the existing Claude widget. Super+Shift+I
opens or toggles it. Escape and focus loss dismiss it. A normal window mode aids
inspection. No new background service is necessary.

Visual direction: midnight blue (#172337), porcelain text (#F4F7FC), soft blue
(#94BFFF), muted slate (#ADBACD), amber (#F5C879), and coral (#FF9292). Lato for
interface text and a large weekly percentage. One segmented weekly meter is the
focal point; supporting text shows quota remaining and the local reset date.
Rounded panel, generous spacing, small plan badge and understated refresh footer.

Read ~/.codex/auth.json (or CODEX_HOME) on each request; only send the access token
and account ID to the supplied HTTPS endpoint. Do not copy credentials or cookies.
Do not refresh or mutate the shared login: prompt for codex login if unauthorized.
Weekly means a 604800-second window, in either primary or secondary position.
Missing limits must never look like zero usage. Preserve last successful data on
fetch errors with an explicit stale label; store only normalized usage and timestamp,
scoped to account. Network requests run off the GTK main thread with timeouts.

Verification: unit tests for window selection, invalid/missing values, expiration,
auth and cache isolation; live endpoint fetch; GTK rendering and keyboard behavior;
GNOME shortcut readback; clean git status and pushed commits.
