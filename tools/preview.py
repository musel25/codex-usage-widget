"""Render a synthetic preview without reading credentials or fetching usage."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GDK_BACKEND', 'x11')

import time
import codex_usage_widget as w
from codex_usage_core import Usage, Window

app = w.UsageApplication(normal_window=True)
app.register(None)
provider = w.Gtk.CssProvider()
provider.load_from_data(w.CSS)
w.Gtk.StyleContext.add_provider_for_screen(
    w.Gdk.Screen.get_default(), provider, w.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
with patch.object(w, 'load_cached_usage', return_value=None), \
        patch.object(w.UsageWindow, 'start_fetch'):
    win = w.UsageWindow(app, True)
win.usage = Usage('plus', Window(62, time.time() + 180000, 604800),
                  Window(36, None, 18000), time.time())
win.render()

output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/widget-preview.png')
errors = []


def capture():
    try:
        pix = w.Gdk.pixbuf_get_from_window(win.get_window(), 0, 0, *win.get_size())
        if pix is None:
            raise RuntimeError('The widget window could not be captured')
        pix.savev(str(output), 'png', [], [])
        print(f'Preview saved to {output}')
    except Exception as exc:
        errors.append(exc)
    finally:
        # Both widgets normally shut down their application on destroy.
        with patch.object(w.Gtk, 'main_quit'):
            win.destroy()
        w.Gtk.main_quit()
    return False


w.GLib.timeout_add(500, capture)
w.Gtk.main()
if errors:
    raise SystemExit(str(errors[0]))
