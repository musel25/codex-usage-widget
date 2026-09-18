"""Install local launchers and a GNOME shortcut without replacing other bindings."""
import ast
from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
SCHEMA = 'org.gnome.settings-daemon.plugins.media-keys'
CUSTOM = SCHEMA + '.custom-keybinding'
BASE = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/'
BINDING = '<Super><Shift>i'


def settings(*args):
    return subprocess.check_output(['gsettings', *args], text=True).strip()


def shortcut_matches(value):
    if not isinstance(value, str):
        return False
    modifiers = re.findall(r'<([^>]+)>', value.lower())
    key = re.sub(r'<[^>]+>', '', value).lower()
    return set(modifiers) == {'super', 'shift'} and key == 'i'


def check_builtin_bindings():
    schemas = set(settings('list-schemas').splitlines())
    for schema in (SCHEMA, 'org.gnome.desktop.wm.keybindings',
                   'org.gnome.mutter.keybindings', 'org.gnome.mutter.wayland.keybindings',
                   'org.gnome.shell.keybindings'):
        if schema not in schemas:
            continue
        for line in settings('list-recursively', schema).splitlines():
            _, key, raw = line.split(' ', 2)
            try:
                value = ast.literal_eval(raw.removeprefix('@as '))
            except (ValueError, SyntaxError):
                continue
            values = value if isinstance(value, list) else [value]
            if any(shortcut_matches(item) for item in values):
                raise SystemExit(f'Win+Shift+I is already assigned to {schema}: {key}. Change that shortcut in GNOME Settings first.')


def install():
    uv = shutil.which('uv')
    if not uv:
        raise SystemExit('Install uv first: https://docs.astral.sh/uv/')
    check_builtin_bindings()
    paths = ast.literal_eval(settings('get', SCHEMA, 'custom-keybindings').removeprefix('@as '))
    target = BASE + 'codex-usage/'
    for path in paths:
        binding = ast.literal_eval(settings('get', CUSTOM + ':' + path, 'binding'))
        if shortcut_matches(binding) and path != target:
            name = settings('get', CUSTOM + ':' + path, 'name')
            raise SystemExit(f'Win+Shift+I is already assigned to {name}. Choose another binding in GNOME Settings.')
    bin_dir = Path.home() / '.local/bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, script in [('codex-usage-widget', 'codex_usage_widget.py'), ('codex-usage', 'codex_usage_cli.py')]:
        launcher = bin_dir / name
        launcher.write_text('#!/bin/sh\nexec ' + shlex.join([uv, 'run', '--project', str(ROOT), 'python', str(ROOT / script)]) + ' "$@"\n')
        launcher.chmod(0o755)
    desktop_dir = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
    desktop_dir.mkdir(parents=True, exist_ok=True)
    executable = str(bin_dir / 'codex-usage-widget').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
    (desktop_dir / 'codex-usage-widget.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Codex Usage\nComment=Check your weekly Codex usage\n'
        f'Exec="{executable}"\nIcon=utilities-system-monitor\nTerminal=false\nCategories=Utility;\nStartupWMClass=codex_usage_widget.py\n'
    )
    for key, value in [('name', 'Codex Weekly Usage'), ('command', shlex.quote(str(bin_dir / 'codex-usage-widget'))), ('binding', BINDING)]:
        settings('set', CUSTOM + ':' + target, key, value)
    if target not in paths:
        paths.append(target)
        settings('set', SCHEMA, 'custom-keybindings', repr(paths))
    print('Installed Codex Usage. Press Win+Shift+I to open the widget.')
    print('CLI: ' + str(bin_dir / 'codex-usage'))


if __name__ == '__main__':
    install()
