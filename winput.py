"""Win32 layer: SendInput injection, low-level mouse/keyboard hooks, and the
few window calls the app needs.

Games reading DirectInput / Raw Input ignore virtual-key events, so keys are
sent as hardware scan codes and clicks as raw mouse events.
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = wintypes.WPARAM
LRESULT = ctypes.c_ssize_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x01, 0x02, 0x08

# button -> (down flag, up flag, mouseData)
MOUSE_BUTTONS = {
    "left":   (0x0002, 0x0004, 0),
    "right":  (0x0008, 0x0010, 0),
    "middle": (0x0020, 0x0040, 0),
    "x1":     (0x0080, 0x0100, 1),
    "x2":     (0x0080, 0x0100, 2),
}
# button -> virtual-key code, for GetAsyncKeyState
MOUSE_VK = {"left": 0x01, "right": 0x02, "middle": 0x04, "x1": 0x05, "x2": 0x06}
# hook message -> (button, is_down); None = X button, read from mouseData
MOUSE_MSGS = {0x201: ("left", True), 0x202: ("left", False),
              0x204: ("right", True), 0x205: ("right", False),
              0x207: ("middle", True), 0x208: ("middle", False),
              0x20B: (None, True), 0x20C: (None, False)}

WH_KEYBOARD_LL, WH_MOUSE_LL, HC_ACTION = 13, 14, 0
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
LLMHF_INJECTED = 0x01
WM_QUIT = 0x0012
ERROR_ALREADY_EXISTS = 183


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR))


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR))


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),
                    ("mi", MOUSEINPUT))  # largest member: sizes INPUT
    _anonymous_ = ("_u",)
    _fields_ = (("type", wintypes.DWORD), ("_u", _U))


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR))


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR))


HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int,
                            wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC,
                                     wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
# Without restype, ctypes truncates the 64-bit handle to a 32-bit int
# and SetWindowsHookExW fails with error 126.
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.restype = wintypes.HANDLE
user32.FindWindowW.restype = wintypes.HWND


# ─── Injection ────────────────────────────────────────────────────────────────
def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_key(scan, up, extended=False):
    flags = (KEYEVENTF_SCANCODE
             | (KEYEVENTF_KEYUP if up else 0)
             | (KEYEVENTF_EXTENDEDKEY if extended else 0))
    _send(INPUT(type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(wScan=scan, dwFlags=flags)))


def send_mouse(button, up):
    down_flag, up_flag, data = MOUSE_BUTTONS[button]
    _send(INPUT(type=INPUT_MOUSE,
                mi=MOUSEINPUT(mouseData=data,
                              dwFlags=up_flag if up else down_flag)))


def is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def release_stuck_mouse():
    """Release the mouse buttons Windows still sees as down, e.g. after a
    process was killed mid-press. Only those: a spurious right or side-button
    UP opens a context menu or navigates back. Several passes: a single UP
    doesn't always resync the driver."""
    stuck = [button for button, vk in MOUSE_VK.items() if is_down(vk)]
    for _ in range(3 if stuck else 0):
        for button in stuck:
            send_mouse(button, True)
        time.sleep(0.02)


# English names for keys without a printed character. Windows' own names
# (GetKeyNameTextW) follow the keyboard layout's language.
KEY_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause",
    0x14: "Caps Lock", 0x1B: "Esc", 0x20: "Space", 0x21: "Page Up",
    0x22: "Page Down", 0x23: "End", 0x24: "Home", 0x25: "Left", 0x26: "Up",
    0x27: "Right", 0x28: "Down", 0x2C: "Print Screen", 0x2D: "Insert",
    0x2E: "Delete", 0x5B: "Left Win", 0x5C: "Right Win", 0x5D: "Menu",
    0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -", 0x6E: "Num .", 0x6F: "Num /",
    0x90: "Num Lock", 0x91: "Scroll Lock", 0xA0: "Left Shift",
    0xA1: "Right Shift", 0xA2: "Left Ctrl", 0xA3: "Right Ctrl",
    0xA4: "Left Alt", 0xA5: "Right Alt",
    **{0x60 + i: f"Num {i}" for i in range(10)},
    **{0x70 + i: f"F{i + 1}" for i in range(24)},
}


def key_name(vk, extended):
    """English key label: a fixed name, or the character printed on the key."""
    if vk == 0x0D and extended:
        return "Num Enter"
    if vk in KEY_NAMES:
        return KEY_NAMES[vk]
    char = user32.MapVirtualKeyW(vk, 2) & 0xFFFF  # MAPVK_VK_TO_CHAR, dead-key bit off
    return chr(char).upper() if char > 0x20 else f"Key {vk}"


# ─── Hooks ────────────────────────────────────────────────────────────────────
_hook_thread_id = None


def run_hooks(on_mouse, on_key):
    """Install the low-level hooks and pump messages; blocks until
    stop_hooks(), then removes the hooks.

    on_mouse(button, down) and on_key(vk, scan, extended, down) get physical
    events only (injected ones are skipped) and return True to swallow them.
    Raises OSError if a hook can't be installed.
    """
    def mouse_proc(n_code, w_param, l_param):
        if n_code == HC_ACTION and w_param in MOUSE_MSGS:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not info.flags & LLMHF_INJECTED:
                button, down = MOUSE_MSGS[w_param]
                button = button or ("x1" if info.mouseData >> 16 == 1 else "x2")
                if on_mouse(button, down):
                    return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def key_proc(n_code, w_param, l_param):
        if n_code == HC_ACTION:
            k = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not k.flags & LLKHF_INJECTED:
                if on_key(k.vkCode, k.scanCode, bool(k.flags & LLKHF_EXTENDED),
                          w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)):
                    return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    global _hook_thread_id
    # Stays referenced while this function runs, and the hooks are removed
    # before it returns: the callbacks can't be garbage-collected under them.
    procs = (HOOKPROC(mouse_proc), HOOKPROC(key_proc))
    module = kernel32.GetModuleHandleW(None)
    hooks = []
    for kind, proc in zip((WH_MOUSE_LL, WH_KEYBOARD_LL), procs):
        hook = user32.SetWindowsHookExW(kind, proc, module, 0)
        if not hook:
            error = ctypes.WinError(ctypes.get_last_error())
            # An installed hook without a pump would stall all input.
            for installed in hooks:
                user32.UnhookWindowsHookEx(installed)
            raise error
        hooks.append(hook)

    _hook_thread_id = kernel32.GetCurrentThreadId()
    try:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        for hook in hooks:
            user32.UnhookWindowsHookEx(hook)


def stop_hooks():
    """End run_hooks' message pump from another thread."""
    if _hook_thread_id:
        user32.PostThreadMessageW(_hook_thread_id, WM_QUIT, 0, 0)


# ─── Window helpers ───────────────────────────────────────────────────────────
def single_instance(name, window_class, window_title):
    """Claim a named mutex; False if another process already holds it, in
    which case its window is brought to the front."""
    kernel32.CreateMutexW(None, False, name)  # held until the process exits
    if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
        return True
    hwnd = user32.FindWindowW(window_class, window_title)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return False


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def dark_title_bar(hwnd):
    value = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, 20,  # DWMWA_USE_IMMERSIVE_DARK_MODE
        ctypes.byref(value), ctypes.sizeof(value))
