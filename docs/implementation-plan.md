# Implementation plan

1. Create public repository, README and uv environment. Commit and push initial setup.
2. Implement tested core and CLI: local login, bounded HTTPS fetch, weekly selection
   by duration, normalized cache scoped to account, safe error messages. Cover both
   window positions, unavailable weekly quotas, malformed payloads, cache corruption,
   auth failures and expired reset times. Run `uv run python -m unittest discover -s tests`.
3. Implement GTK3 popup with weekly meter, reset countdown, remaining quota, real plan
   label, loading/error/cached states, manual refresh, Escape, focus dismissal and
   single-instance toggle. Inspect actual render and live values. Commit and push.
4. Install launcher and desktop entry; register Super+Shift+I preserving every existing
   binding. Verify launcher and binding, document usage, commit and push.
