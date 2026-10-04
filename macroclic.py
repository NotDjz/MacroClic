"""MacroClic: repeat or hold a key / mouse button, toggled by another one.

The trigger works from anywhere, in game too, and the game never sees it.
"""

import atexit
import json
import math
import os
import random
import sys
import threading
import time
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import font, ttk

import sv_ttk

import winput

APP = "MacroClic"
PRESS_TIME = 0.04          # how long a repeated press stays down (seconds)
MIN_INTERVAL, MAX_INTERVAL = 0.05, 3600
MAX_JITTER = 90            # percent; at 100 a wait could shrink to nothing
CONFIG = Path(os.environ.get("APPDATA", ".")) / APP / "config.json"

MOUSE_NAMES = {"left": "Left click", "right": "Right click",
               "middle": "Middle click", "x1": "Mouse button 4",
               "x2": "Mouse button 5"}


# ─── Inputs ───────────────────────────────────────────────────────────────────
# An input is a dict: {"id": "mouse:x1", "name": ...} or
# {"id": "key:65:0", "name": "A", "scan": 30, "ext": False}. The key id
# includes ext: Enter and numpad Enter share a VK code.

def key_id(vk, ext):
    return f"key:{vk}:{int(ext)}"


ESCAPE = key_id(0x1B, False)  # cancels a capture


def mouse_input(button):
    return {"id": "mouse:" + button, "name": MOUSE_NAMES[button]}


def key_input(vk, scan, ext):
    return {"id": key_id(vk, ext),
            "name": winput.key_name(vk, ext),
            "scan": scan, "ext": ext}


def renamed(inp):
    """Copy with the display name rebuilt: names on disk may be stale (older
    version, other keyboard layout) and aren't trusted."""
    kind, _, value = inp["id"].partition(":")
    if kind == "mouse":
        return mouse_input(value)
    return key_input(int(value.partition(":")[0]), inp["scan"], inp["ext"])


def vk_of(inp):
    kind, _, value = inp["id"].partition(":")
    return winput.MOUSE_VK[value] if kind == "mouse" else int(value.partition(":")[0])


def release_if_down(inp):
    """UP only if Windows sees it down: a stray right or side-button UP opens
    a context menu or navigates back in the window in front."""
    if winput.is_down(vk_of(inp)):
        send(inp, True)


def send(inp, up):
    kind, _, value = inp["id"].partition(":")
    if kind == "mouse":
        winput.send_mouse(value, up)
    else:
        winput.send_key(inp["scan"], up, inp["ext"])


def valid(inp, role):
    """role = "trigger" | "action". Left click can't be the trigger: it would
    be swallowed system-wide, including clicks on this window."""
    if not (isinstance(inp, dict) and isinstance(inp.get("id"), str)):
        return False
    kind, _, value = inp["id"].partition(":")
    if kind == "mouse":
        return value in MOUSE_NAMES and not (role == "trigger" and value == "left")
    vk, ext = value.partition(":")[0], inp.get("ext")
    return (kind == "key" and vk.isdigit() and isinstance(ext, bool)
            and inp["id"] == key_id(vk, ext) and isinstance(inp.get("scan"), int))


def parse_number(value, low, high):
    """value clamped to low..high, or None if not a finite number. Typed
    text may use a decimal comma; True / False aren't numbers here."""
    if isinstance(value, bool):
        return None
    try:
        value = float(str(value).replace(",", "."))
    except ValueError:
        return None
    return min(high, max(low, value)) if math.isfinite(value) else None


def parse_interval(value):
    """Seconds between presses."""
    return parse_number(value, MIN_INTERVAL, MAX_INTERVAL)


def parse_jitter(value):
    """Whole percent for Anti-detection."""
    value = parse_number(value, 1, MAX_JITTER)
    return None if value is None else round(value)


# ─── Settings ─────────────────────────────────────────────────────────────────
cfg = {"trigger": mouse_input("x1"), "action": mouse_input("left"),
       "interval": 0.2, "hold": False, "random": False, "jitter": 50}


def load_config():
    try:
        saved = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return
    for k in ("trigger", "action"):
        if valid(saved.get(k), k):
            cfg[k] = renamed(saved[k])
    for k, parse in (("interval", parse_interval), ("jitter", parse_jitter)):
        if (value := parse(saved.get(k))) is not None:
            cfg[k] = value
    for k in ("hold", "random"):
        if isinstance(saved.get(k), bool):
            cfg[k] = saved[k]


def save_config():
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


# ─── Hooks ────────────────────────────────────────────────────────────────────
active = False
stopping = False
capture = None     # "trigger" | "action" while waiting for a press
held = set()       # physical inputs currently down: tells auto-repeats apart
swallowed = set()  # ids whose DOWN we swallowed: swallow repeats and the UP
hook_error = None


def handle(inp_id, down, make):
    """Called for every physical key / button event. True = swallow it."""
    global active, capture
    repeat = down and inp_id in held
    (held.add if down else held.discard)(inp_id)
    if inp_id in swallowed:
        if not down:
            swallowed.discard(inp_id)
        return True
    # Repeats of a key already down before capture / toggle must not count:
    # its DOWN reached the system, so its UP must too.
    if not down or repeat:
        return False
    if capture:
        if inp_id == ESCAPE:
            capture = None
        else:
            inp = make()
            if not valid(inp, capture):
                return False
            cfg[capture], capture = inp, None
    elif inp_id == cfg["trigger"]["id"]:
        active = not active
    else:
        return False
    swallowed.add(inp_id)
    return True


def on_mouse(button, down):
    inp = mouse_input(button)
    return handle(inp["id"], down, lambda: inp)


def on_key(vk, scan, ext, down):
    return handle(key_id(vk, ext), down,
                  lambda: key_input(vk, scan, ext))


def hook_thread():  # runs in _hooks
    global hook_error
    try:
        winput.run_hooks(on_mouse, on_key)
    except OSError as e:
        hook_error = str(e)


# ─── Press loop ───────────────────────────────────────────────────────────────
def wait_while_active(seconds):
    end = time.monotonic() + seconds
    while active and not stopping and time.monotonic() < end:
        time.sleep(0.01)


def next_gap():
    """Wait after a repeated press, drawn within ±jitter % when Anti-detection
    is on: a perfectly regular rhythm is easy for anti-cheat to spot. The draw
    spreads the gap, not the whole interval, so it never goes below zero."""
    spread = cfg["jitter"] / 100 if cfg["random"] else 0
    return (cfg["interval"] - PRESS_TIME) * random.uniform(1 - spread, 1 + spread)


def worker():
    while not stopping:
        if not active:
            time.sleep(0.02)
            continue
        # Snapshot: changing the action mid-run must still release the old one.
        inp = cfg["action"]
        try:
            send(inp, False)
            wait_while_active(float("inf") if cfg["hold"] else PRESS_TIME)
        finally:
            send(inp, True)  # never leave it stuck down
        wait_while_active(next_gap())


_worker = threading.Thread(target=worker, daemon=True)
_hooks = threading.Thread(target=hook_thread, daemon=True)


def shutdown():
    global active, stopping
    active, stopping = False, True
    _worker.join(timeout=1.0)
    release_if_down(cfg["action"])
    winput.stop_hooks()
    if _hooks.is_alive():
        _hooks.join(timeout=1.0)


# ─── Windows integration ──────────────────────────────────────────────────────
def system_theme():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft"
                            r"\Windows\CurrentVersion\Themes\Personalize") as k:
            light = winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
    except OSError:
        return "light"
    return "light" if light else "dark"


def resource(name):
    """Bundled file: next to the script, or in PyInstaller's temp dir."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


# ─── Window ───────────────────────────────────────────────────────────────────
# sv-ttk's own background / text / accent, plus keycap shades.
PALETTE = {
    "dark":  {"bg": "#1c1c1c", "fg": "#fafafa", "dim": "#9d9d9d",
              "face": "#2d2d2d", "edge": "#454545", "lip": "#0f0f0f",
              "accent": "#57c8ff", "on_accent": "#1c1c1c", "error": "#ff99a4"},
    "light": {"bg": "#fafafa", "fg": "#1c1c1c", "dim": "#616161",
              "face": "#ffffff", "edge": "#c9c9c9", "lip": "#b4b4b4",
              "accent": "#005fb8", "on_accent": "#ffffff", "error": "#c42b1c"},
}


class Keycap(tk.Canvas):
    """An input drawn as a key. Click it, or focus it and press Space or
    Enter, to rebind it."""

    def __init__(self, parent, pal, scale, key_font, command):
        self.pal, self.scale, self.font = pal, scale, key_font
        self.width, self.height = round(250 * scale), round(52 * scale)
        super().__init__(parent, width=self.width, height=self.height,
                         bg=pal["bg"], highlightthickness=0, takefocus=1)
        self.command = command
        self.focused = False
        self.view = self.drawn = None
        for seq in ("<Button-1>", "<space>", "<Return>"):
            self.bind(seq, self._invoke)
        self.bind("<FocusIn>", lambda _: self._focus(True))
        self.bind("<FocusOut>", lambda _: self._focus(False))

    def _invoke(self, _):
        if self.view and self.view[2]:
            self.command()

    def _focus(self, focused):
        self.focused = focused
        self._draw()

    def stretch(self, width):
        self.width, self.drawn = width, None
        self.configure(width=width)
        self._draw()

    def show(self, text, capturing, enabled):
        self.view = (text, capturing, enabled)
        self._draw()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
        self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        if self.view is None or (self.view, self.focused) == self.drawn:
            return
        self.drawn = (self.view, self.focused)
        text, capturing, enabled = self.view
        self.configure(cursor="hand2" if enabled else "")
        p, s = self.pal, self.scale
        w, h = self.width - 1, self.height - 1
        lip, r = round(5 * s), round(9 * s)
        ring = capturing or self.focused
        self.delete("all")
        self._round_rect(0, lip, w, h, r, fill=p["lip"], outline=p["lip"])
        self._round_rect(0, 0, w, h - lip, r,
                         fill=p["accent"] if capturing else p["face"],
                         outline=p["accent"] if ring else p["edge"],
                         width=max(1, round(s * (2 if ring else 1))))
        color = (p["on_accent"] if capturing
                 else p["fg"] if enabled else p["dim"])
        self.create_text(w / 2, (h - lip) / 2, text=text, fill=color,
                         font=self.font)


def start_capture(role):
    global capture
    if not active:  # the keycap's enabled look can lag by one refresh
        capture = role


def pick_family(available, *families):
    return next((f for f in families if f in available), "Segoe UI")


def build_ui(root):
    theme = system_theme()
    sv_ttk.set_theme(theme)
    pal = PALETTE[theme]
    scale = root.winfo_fpixels("1i") / 96
    families = set(font.families())
    text_family = pick_family(families, "Segoe UI Variable Text")
    key_font = (pick_family(families, "Segoe UI Variable Display Semib",
                            "Segoe UI Semibold"), 12)
    small = (text_family, 10)

    body = ttk.Frame(root, padding=(22, 18, 22, 16))
    body.pack(fill="both")

    def caption(text, top):
        ttk.Label(body, text=text, font=small, foreground=pal["dim"]).pack(
            anchor="w", pady=(top, 6))

    keycaps = {}
    for key, text, top in (("trigger", "When I press", 0),
                           ("action", "the macro sends", 14)):
        caption(text, top)
        keycaps[key] = Keycap(body, pal, scale, key_font,
                              lambda k=key: start_capture(k))
        keycaps[key].pack(anchor="w")

    mode_row = ttk.Frame(body)
    mode_row.pack(anchor="w", pady=(18, 0))
    hold = tk.BooleanVar(value=cfg["hold"])
    modes = []
    for text, value, gap in (("Repeat", False, 4), ("Hold", True, 14)):
        mode = ttk.Radiobutton(mode_row, text=text, value=value, variable=hold,
                               style="Toggle.TButton", width=9,
                               command=lambda: cfg.update(hold=hold.get()))
        mode.pack(side="left", padx=(0, gap))
        modes.append(mode)
    every = ttk.Label(mode_row, text="every", font=small)
    every.pack(side="left")
    interval = tk.StringVar(value=f"{cfg['interval']:.2f}")
    spin = ttk.Spinbox(mode_row, from_=MIN_INTERVAL, to=MAX_INTERVAL, increment=0.05,
                       width=6, textvariable=interval, format="%.2f")
    spin.pack(side="left", padx=6)
    unit = ttk.Label(mode_row, text="s", font=small)
    unit.pack(side="left")

    def bind_number(var, key, parse):
        def on_write(*_):
            value = parse(var.get())
            if value is not None:  # half-typed value: keep the previous one
                cfg[key] = value
        var.trace_add("write", on_write)

    bind_number(interval, "interval", parse_interval)

    random_row = ttk.Frame(body)
    random_row.pack(anchor="w", pady=(12, 0))
    rand = tk.BooleanVar(value=cfg["random"])
    check = ttk.Checkbutton(random_row, text="Anti-detection", variable=rand,
                            command=lambda: cfg.update(random=rand.get()))
    check.pack(side="left")
    plus = ttk.Label(random_row, text="±", font=small)
    plus.pack(side="left", padx=(14, 0))
    jitter = tk.StringVar(value=str(cfg["jitter"]))
    jitter_spin = ttk.Spinbox(random_row, from_=5, to=MAX_JITTER, increment=5,
                              width=4, textvariable=jitter)
    jitter_spin.pack(side="left", padx=6)
    percent = ttk.Label(random_row, text="%", font=small)
    percent.pack(side="left")
    bind_number(jitter, "jitter", parse_jitter)
    root.update_idletasks()  # keycaps as wide as the mode row
    for cap in keycaps.values():
        cap.stretch(mode_row.winfo_reqwidth())

    band = tk.Label(root, anchor="w", justify="left", height=2,
                    padx=round(22 * scale), pady=round(6 * scale),
                    wraplength=mode_row.winfo_reqwidth(),
                    font=(text_family, 10, "bold"))
    band.pack(fill="x")

    # Inputs are replaced, never mutated, so a shallow copy spots any change.
    shown, saved = None, dict(cfg)

    def refresh():
        nonlocal shown, saved
        if cfg != saved:  # save right away: survives logoff or a kill
            saved = dict(cfg)
            save_config()
        root.after(100, refresh)
        for key, cap in keycaps.items():
            cap.show("Press a key or button…" if capture == key
                     else cfg[key]["name"], capture == key, not active)
        trig = cfg["trigger"]["name"]
        if hook_error:
            text, bg, fg = (f"Trigger unavailable: {hook_error}",
                            pal["face"], pal["error"])
        elif capture == "trigger":
            text, bg, fg = ("Press the key or mouse button that will start "
                            "the macro (not left click). Esc to cancel.",
                            pal["face"], pal["fg"])
        elif capture == "action":
            text, bg, fg = ("Press the key or mouse button the macro should "
                            "send. Esc to cancel.", pal["face"], pal["fg"])
        elif active:
            text, bg, fg = (f"Macro running. {trig} to stop.",
                            pal["accent"], pal["on_accent"])
        else:
            text, bg, fg = (f"Macro stopped. {trig} to start.",
                            pal["face"], pal["dim"])
        off = active or cfg["hold"]
        jitter_off = off or not cfg["random"]
        if (text, bg, fg, active, off, jitter_off) == shown:
            return  # Tk repaints on every configure, even with same values
        shown = (text, bg, fg, active, off, jitter_off)
        band.configure(text=text, bg=bg, fg=fg)
        # Switching mode mid-hold would never release the held input.
        for mode in modes:
            mode.state(["disabled"] if active else ["!disabled"])
        for disabled, widgets, labels in (
                (off, (spin, check), (every, unit)),
                (jitter_off, (jitter_spin,), (plus, percent))):
            for widget in widgets:
                widget.state(["disabled" if disabled else "!disabled"])
            for label in labels:  # sv-ttk doesn't grey disabled labels
                label.configure(foreground=pal["dim"] if disabled else pal["fg"])

    refresh()
    if theme == "dark":
        root.update_idletasks()
        winput.dark_title_bar(int(root.wm_frame(), 16))


def main():
    # Two instances would toggle on the same trigger and cancel each other.
    if not winput.single_instance("MacroClic.SingleInstance", "TkTopLevel", APP):
        return
    load_config()
    # Clean up after a previous run killed mid-press (Task Manager, crash).
    winput.release_stuck_mouse()
    release_if_down(cfg["action"])
    winput.enable_dpi_awareness()

    _hooks.start()
    _worker.start()
    atexit.register(shutdown)

    root = tk.Tk()
    root.withdraw()  # show once styled: no flash of the default look
    root.title(APP)
    root.resizable(False, False)
    root.iconbitmap(default=resource("macroclic.ico"))
    build_ui(root)

    def close():
        shutdown()
        save_config()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
