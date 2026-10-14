# Contributing

Bug reports, small fixes, and design improvements are welcome. This is an
unofficial Linux GTK3 utility, maintained independently of Codex.

## Development

Install `uv`, system GTK3, PyGObject and Cairo (Ubuntu packages: `python3-gi`,
`python3-gi-cairo`, `gir1.2-gtk-3.0`). From your clone:

```sh
uv venv --python /usr/bin/python3 --system-site-packages
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run python tools/preview.py /tmp/codex-preview.png
```

The preview briefly opens a window with synthetic data and writes a screenshot.
It does not read credentials or call the usage endpoint. A graphical session is
required; in a headless environment install `xvfb` and prefix the preview command
with `xvfb-run -a`. CI runs tests and renders the widget on Ubuntu 22.04 and 24.04.

Keep pull requests focused. Describe the visible change, how to reproduce it,
and how you checked it. Include screenshots with synthetic data for UI changes.
Add regression tests for parsing, authentication, cache, or refresh fixes.
Use `uv add` for dependencies and include the updated lockfile.

## Credentials and reports

Never attach cookies, tokens, `auth.json`, `config.env`, or raw authenticated
network requests to issues or pull requests. Use synthetic fixtures in tests.
For a suspected credential exposure, use GitHub's private vulnerability reporting
option if available; do not post the secret publicly.

## Companion projects

[Claude Usage Widget](https://github.com/musel25/claude-usage-widget)
shares the visual layout. The projects remain separately installable because
their authentication and setup differ. UI improvements can be ported between
them without making either provider a dependency of the other.

Contributions are licensed under the repository's [MIT license](LICENSE).
