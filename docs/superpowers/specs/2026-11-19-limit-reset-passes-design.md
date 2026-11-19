# Limit reset passes

Show available banked limit reset passes and their expiry in the GTK widget and terminal summary. Keep regular usage refresh times separate. Read the signed-in Codex reset credit list. Parse only the pass title and expiry. Show local expiry time and time left. An unavailable pass response must not hide regular usage. Do not redeem passes.

## Approaches

- Automatic read from Codex reset credit list: selected. It follows changes in the account.
- Manual dates in config: requires updates when passes are used or changed.
- Browser scraping: depends on page layout.
