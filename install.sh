#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    uv venv --python /usr/bin/python3 --system-site-packages
fi
uv sync
uv run python -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk; import cairo' || {
    echo 'GTK3 is required. On Ubuntu: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0' >&2
    exit 1
}
uv run python install.py
