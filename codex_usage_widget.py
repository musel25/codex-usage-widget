"""Native weekly Codex usage popup. Run with uv run python codex_usage_widget.py."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
import threading
import time

# GNOME honors popup placement and above-window hints through XWayland.
if os.environ.get('DISPLAY'):
    os.environ.setdefault('GDK_BACKEND', 'x11')

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from codex_usage_core import (
    Usage, UsageError, current_account_key, fetch_usage, load_cached_usage, reset_text, save_cached_usage,
)

CSS = b'''
window { background: transparent; font-family: Lato, sans-serif; }
.panel { background: #172337; border: 1px solid #3a4b64; border-radius: 22px; }
label { color: #f4f7fc; }
.title { font-size: 22px; font-weight: 800; }
.subtitle { color: #adbacd; font-size: 13px; }
.badge { color: #c4d9fa; background: #2a3d58; border-radius: 8px; padding: 5px 10px; font-size: 11px; font-weight: 700; }
.value { font-size: 66px; font-weight: 300; letter-spacing: -2px; }
.unit { color: #adbacd; font-size: 15px; padding-bottom: 15px; }
.remaining { color: #94bfff; font-size: 15px; font-weight: 700; }
.detail { font-size: 15px; font-weight: 700; }
.caption { color: #adbacd; font-size: 12px; }
.status { color: #adbacd; font-size: 11px; }
.warning { color: #f5c879; }
.error { color: #ff9292; }
.reset-box { background: #203047; border-radius: 12px; padding: 16px; }
button { background: transparent; background-image: none; color: #c4d9fa; border: 1px solid #475975; border-radius: 8px; padding: 7px 12px; box-shadow: none; text-shadow: none; font-size: 12px; }
button:hover { background: #304561; border-color: #94bfff; }
button:focus { border-color: #94bfff; outline: 2px solid #94bfff; outline-offset: 2px; }
button:disabled { color: #7a8aa1; border-color: #304159; }
.close { border: none; padding: 3px 6px; font-size: 18px; }
separator { background: #35445a; min-height: 1px; }
'''


def label(text: str, style: str, xalign: float = 0) -> Gtk.Label:
    item = Gtk.Label(label=text, xalign=xalign)
    item.get_style_context().add_class(style)
    return item


class Meter(Gtk.DrawingArea):
    """A quiet segmented meter, with a partially filled final segment."""

    def __init__(self):
        super().__init__()
        self.percent = None
        self.set_size_request(-1, 26)
        self.connect('draw', self.draw_meter)
        self.get_accessible().set_name('Weekly usage meter')

    def update(self, percent):
        self.percent = percent
        self.get_accessible().set_description(
            'Weekly usage unavailable' if percent is None else f'{percent:g}% of weekly usage used'
        )
        self.queue_draw()

    def draw_meter(self, _widget, cr):
        count, gap, height = 32, 4, 12
        width = (self.get_allocated_width() - gap * (count - 1)) / count
        y = (self.get_allocated_height() - height) / 2
        color = (0.58, 0.75, 1.0)
        if self.percent is not None and self.percent >= 90:
            color = (1.0, 0.57, 0.57)
        elif self.percent is not None and self.percent >= 75:
            color = (0.96, 0.78, 0.47)
        filled = min(100, max(0, self.percent or 0)) / 100 * count
        for i in range(count):
            x = i * (width + gap)
            cr.set_source_rgb(0.21, 0.29, 0.40)
            cr.rectangle(x, y, width, height)
            cr.fill()
            fraction = min(1, max(0, filled - i))
            if fraction:
                cr.set_source_rgb(*color)
                cr.rectangle(x, y, width * fraction, height)
                cr.fill()
        return False


class UsageWindow(Gtk.ApplicationWindow):
    def __init__(self, app, normal_window=False):
        super().__init__(application=app, title='Codex Usage')
        self.usage: Usage | None = None
        self.busy = False
        self.last_attempt = 0
        self.alive = True
        self.error_message = None
        self.cached = False
        self.set_default_size(424, -1)
        self.set_resizable(False)
        self.set_decorated(normal_window)
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        if not normal_window:
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.connect('key-press-event', self.on_key)
        self.connect('destroy', self.on_destroy)
        if not normal_window:
            self.connect('focus-out-event', self.on_focus_out)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.get_style_context().add_class('panel')
        self.add(panel)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(content, f'set_margin_{side}')(28)
        panel.add(content)

        header = Gtk.Box(spacing=10)
        header.pack_start(label('Codex', 'title'), False, False, 0)
        self.plan = label('Usage', 'badge')
        self.plan.set_valign(Gtk.Align.CENTER)
        header.pack_start(self.plan, False, False, 0)
        close = Gtk.Button(label='\u00d7')
        close.get_style_context().add_class('close')
        close.set_tooltip_text('Close (Esc)')
        close.get_accessible().set_name('Close widget')
        close.connect('clicked', lambda *_: self.destroy())
        header.pack_end(close, False, False, 0)
        content.add(header)
        subtitle = label('Your week in code', 'subtitle')
        subtitle.set_margin_top(6)
        subtitle.set_margin_bottom(22)
        content.add(subtitle)

        content.add(label('Weekly usage', 'detail'))
        hero = Gtk.Box(spacing=12)
        self.value = label('\u2014', 'value')
        self.unit = label('used', 'unit')
        self.unit.set_valign(Gtk.Align.END)
        hero.add(self.value)
        hero.add(self.unit)
        content.add(hero)
        self.meter = Meter()
        content.add(self.meter)
        self.remaining = label('Checking your available usage\u2026', 'remaining')
        self.remaining.set_margin_top(8)
        self.remaining.set_margin_bottom(24)
        content.add(self.remaining)

        reset_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        reset_box.get_style_context().add_class('reset-box')
        reset_box.add(label('Weekly reset', 'caption'))
        self.reset = label('Fetching your limits\u2026', 'detail')
        self.reset_date = label(' ', 'caption')
        reset_box.add(self.reset)
        reset_box.add(self.reset_date)
        content.add(reset_box)
        self.session = label('', 'caption')
        self.session.set_margin_top(14)
        content.add(self.session)
        self.message = label('', 'caption')
        self.message.set_line_wrap(True)
        self.message.set_max_width_chars(43)
        self.message.set_margin_top(14)
        content.add(self.message)
        divider = Gtk.Separator()
        divider.set_margin_top(20)
        divider.set_margin_bottom(16)
        content.add(divider)
        footer = Gtk.Box(spacing=12)
        self.status = label('Connecting\u2026', 'status')
        footer.pack_start(self.status, True, True, 0)
        self.refresh_button = Gtk.Button(label='Refresh')
        self.refresh_button.set_tooltip_text('Refresh usage (R)')
        self.refresh_button.connect('clicked', lambda *_: self.start_fetch())
        footer.pack_end(self.refresh_button, False, False, 0)
        content.add(footer)
        hint = label('Win + Shift + I to toggle  \u00b7  Esc to close', 'status')
        hint.set_margin_top(16)
        content.add(hint)
        self.show_all()
        self.message.hide()
        self.session.hide()
        self.refresh_button.grab_focus()
        self.timer = GLib.timeout_add_seconds(30, self.tick)
        try:
            cached = load_cached_usage()
        except (UsageError, OSError, ValueError):
            cached = None
        if cached:
            self.usage, self.cached = cached, True
            self.render()
        self.start_fetch()

    def on_destroy(self, *_):
        self.alive = False
        if getattr(self, 'timer', None):
            GLib.source_remove(self.timer)
            self.timer = None

    def on_focus_out(self, *_):
        def dismiss():
            if self.alive and not self.is_active():
                self.destroy()
            return False
        GLib.timeout_add(180, dismiss)
        return False

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        if event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.start_fetch()
            return True
        return False

    def tick(self):
        if not self.alive:
            return False
        self.render()
        if time.monotonic() - self.last_attempt >= 300:
            self.start_fetch()
        return True

    def start_fetch(self):
        if self.busy or not self.alive:
            return
        self.busy = True
        self.last_attempt = time.monotonic()
        self.refresh_button.set_sensitive(False)
        self.status.set_text('Updating cached usage\u2026' if self.usage else 'Connecting\u2026')

        def work():
            try:
                usage = fetch_usage()
                try:
                    save_cached_usage(usage)
                except (OSError, UsageError):
                    pass  # A read-only cache must not hide a successful response.
                GLib.idle_add(self.finish_fetch, usage, None)
            except UsageError as exc:
                GLib.idle_add(self.finish_fetch, None, str(exc))
            except Exception:
                GLib.idle_add(self.finish_fetch, None, 'Unable to read usage. Try refreshing or run codex login.')
        threading.Thread(target=work, daemon=True).start()

    def finish_fetch(self, usage, error):
        if not self.alive:
            return False
        self.busy = False
        self.refresh_button.set_sensitive(True)
        self.error_message = error
        if usage:
            self.usage = usage
            self.cached = False
        elif self.usage:
            self.cached = True
        self.render()
        return False

    def render(self):
        usage = self.usage
        if usage and usage.account_key:
            try:
                same_account = usage.account_key == current_account_key()
            except UsageError:
                same_account = False
            if not same_account:
                self.usage = usage = None
                self.error_message = 'Codex account changed. Refresh to load your current account.'
                self.value.set_text('—')
                self.meter.update(None)
                self.plan.set_text('Usage')
                self.session.hide()
        self.message.set_text(self.error_message or '')
        self.message.get_style_context().add_class('warning')
        self.message.set_visible(bool(self.error_message))
        if not usage:
            if self.error_message:
                self.remaining.set_text('Usage unavailable')
                self.reset.set_text('Connect your Codex account')
                self.reset_date.set_text('Run codex login, then refresh')
                self.status.set_text('Could not update')
            return
        plans = {'prolite': 'Pro Lite', 'pro': 'Pro', 'plus': 'Plus', 'team': 'Team', 'business': 'Business', 'enterprise': 'Enterprise', 'free': 'Free'}
        self.plan.set_text(plans.get(usage.plan, usage.plan.replace('_', ' ').title())[:28])
        week = usage.weekly
        if week:
            self.value.set_text(f'{week.used_percent:g}%')
            self.meter.update(week.used_percent)
            self.remaining.set_text(f'{max(0, 100 - week.used_percent):g}% remaining this week')
            self.reset.set_text(('In ' + reset_text(week.reset_at)) if week.reset_at and week.reset_at > datetime.now().timestamp() else reset_text(week.reset_at))
            if week.reset_at:
                self.reset_date.set_text(datetime.fromtimestamp(week.reset_at).astimezone().strftime('%a, %b %-d at %-I:%M %p %Z'))
            else:
                self.reset_date.set_text('Reset time not provided')
        else:
            self.value.set_text('\u2014')
            self.meter.update(None)
            self.remaining.set_text('Weekly limit not reported')
            self.reset.set_text('No reset time available')
            self.reset_date.set_text('Your account did not return a weekly window')
        self.session.set_visible(usage.session is not None)
        if usage.session:
            self.session.set_text(f'5-hour usage: {usage.session.used_percent:g}% used')
        age = max(0, int((datetime.now().timestamp() - usage.fetched_at) / 60))
        age_text = 'just now' if age == 0 else f'{age}m ago' if age < 60 else f'{age // 60}h ago'
        self.status.set_text(f'{"Saved" if self.cached else "Updated"} {age_text}' + (' \u00b7 stale' if self.cached else ''))
        self.status.get_style_context().remove_class('warning')
        if self.cached:
            self.status.get_style_context().add_class('warning')


class UsageApplication(Gtk.Application):
    def __init__(self, normal_window=False):
        super().__init__(application_id='dev.musel.CodexUsage', flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.normal_window = normal_window

    def do_activate(self):
        existing = self.get_active_window()
        if existing:
            existing.destroy()
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window = UsageWindow(self, self.normal_window)
        window.present()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', action='store_true', help='Stay open as a regular window')
    args = parser.parse_args()
    return UsageApplication(args.window).run([])


if __name__ == '__main__':
    raise SystemExit(main())
