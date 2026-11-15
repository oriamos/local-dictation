"""Global hotkey via a low-level keyboard hook (WH_KEYBOARD_LL).

A hook (rather than RegisterHotKey) is needed because push-to-talk requires key-UP events and
modifier-only hotkeys (Right Ctrl, Ctrl+Win). The callback does no work itself: it only posts
events to the owner, so Windows never drops the hook for being slow.

Events (called on the hook thread; the app forwards them to Qt via signals):
  on_down()   – the full combo became held
  on_up()     – the combo was broken (any of its keys released)
  on_other()  – another key was pressed while the combo was held (it was a shortcut, not dictation)
  on_escape() – Esc pressed (cancel)
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_QUIT = 13, 0x100, 0x101, 0x104, 0x105, 0x12
LLKHF_INJECTED = 0x10
VK_ESCAPE = 0x1B

GROUPS = {
    "ctrl": {0x11, 0xA2, 0xA3}, "alt": {0x12, 0xA4, 0xA5}, "shift": {0x10, 0xA0, 0xA1}, "win": {0x5B, 0x5C},
    "right_ctrl": {0xA3}, "left_ctrl": {0xA2}, "right_alt": {0xA5}, "right_shift": {0xA1},
    "left_win": {0x5B}, "right_win": {0x5C}, "caps_lock": {0x14}, "scroll_lock": {0x91}, "pause": {0x13},
    "insert": {0x2D}, "space": {0x20},
    **{f"f{i}": {0x6F + i} for i in range(1, 25)},
}
MODIFIERS = set().union(GROUPS["ctrl"], GROUPS["alt"], GROUPS["shift"], GROUPS["win"])

KEY_LABELS = {"right_ctrl": "Right Ctrl", "ctrl+win": "Ctrl + Win", "right_alt": "Right Alt",
              "caps_lock": "Caps Lock", "f13": "F13", "ctrl+shift+space": "Ctrl + Shift + Space"}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


VK_NAMES = {0xA2: "left_ctrl", 0xA3: "right_ctrl", 0xA0: "shift", 0xA1: "right_shift", 0xA4: "alt", 0xA5: "right_alt",
            0x5B: "left_win", 0x5C: "right_win", 0x14: "caps_lock", 0x91: "scroll_lock", 0x13: "pause",
            0x2D: "insert", 0x20: "space", **{0x6F + i: f"f{i}" for i in range(1, 25)}}
LABELS = {"left_ctrl": "Left Ctrl", "right_ctrl": "Right Ctrl", "shift": "Shift", "right_shift": "Right Shift",
          "alt": "Alt", "right_alt": "Right Alt", "left_win": "Win", "right_win": "Right Win", "win": "Win",
          "ctrl": "Ctrl", "caps_lock": "Caps Lock", "space": "Space", "f23": "Copilot (F23)"}


def parse(spec: str) -> list[set[int]]:
    out = []
    for p in spec.split("+"):
        p = p.strip().lower()
        out.append({int(p[3:], 16)} if p.startswith("vk_") else GROUPS[p])
    return out


def vk_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"vk_{vk:02x}")


def label(spec: str) -> str:
    return " + ".join(LABELS.get(p, p.upper().replace("_", " ")) for p in spec.split("+"))


def spec_from_combo(vks: list[int]) -> str:
    """Keys held together at peak → hotkey spec. The Copilot key sends Win+Shift+F23: use F23 alone
    (and swallow it), so Windows does not also open Copilot. Any F13–F24 key wins the same way."""
    for vk in vks:
        if 0x7C <= vk <= 0x87:  # F13..F24
            return vk_name(vk)
    names, seen = [], set()
    order = [0xA2, 0xA3, 0x5B, 0x5C, 0xA0, 0xA1, 0xA4, 0xA5]
    for vk in sorted(vks, key=lambda v: order.index(v) if v in order else 99):
        n = vk_name(vk)
        if n not in seen:
            seen.add(n); names.append(n)
    return "+".join(names)


class HotkeyHook:
    def __init__(self, spec: str, on_down, on_up, on_other, on_escape):
        self.on_down, self.on_up, self.on_other, self.on_escape = on_down, on_up, on_other, on_escape
        self.accept_injected = False  # tests only
        self.capture = None           # when set: callable(vk, down) receives every key; normal handling paused
        self.set_spec(spec)
        self._held: set[int] = set()
        self._active = False
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._proc = HOOKPROC(self._callback)  # keep a reference or it gets garbage-collected

    def set_spec(self, spec: str):
        self.spec = spec
        self.groups = parse(spec)
        self.keys = set().union(*self.groups)
        # A lone non-modifier key (F13, Caps Lock…) is swallowed so it doesn't also type/toggle.
        self.suppress = not (self.keys & MODIFIERS)

    def _callback(self, code, wparam, lparam):
        if code == 0:
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if self.accept_injected or not (k.flags & LLKHF_INJECTED):  # ignore our own Ctrl+V / typed text
                vk, down = k.vkCode, wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self.capture is not None:  # "pick a hotkey" dialog is open
                    try:
                        self.capture(vk, down)
                    except Exception:
                        pass
                    return user32.CallNextHookEx(None, code, wparam, lparam)
                ours = vk in self.keys
                try:
                    if down:
                        if vk == VK_ESCAPE:
                            self.on_escape()
                        is_repeat = vk in self._held
                        self._held.add(vk)
                        if not is_repeat:
                            if not self._active and all(self._held & g for g in self.groups):
                                self._active = True
                                self.on_down()
                            elif self._active and not ours:
                                self.on_other()
                    else:
                        self._held.discard(vk)
                        if self._active and ours and not all(self._held & g for g in self.groups):
                            self._active = False
                            self.on_up()
                except Exception:
                    pass
                if ours and self.suppress:
                    return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        h = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg)); user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(h)

    def start(self):
        self._held.clear(); self._active = False
        self._thread = threading.Thread(target=self._run, name="hotkey-hook", daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(1.0)
        self._thread, self._tid = None, 0

    def restart(self):
        """Re-install (after sleep/resume Windows may silently drop LL hooks)."""
        self.stop(); self.start()


DIALOG_HINT = "מתאים: Right Ctrl, Right Alt, מקש Copilot, Caps Lock, F13–F24, או צירוף כמו Ctrl + Win."
