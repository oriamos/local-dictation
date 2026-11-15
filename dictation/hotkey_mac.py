"""Global hotkey on macOS via a Quartz event tap (same API as hotkey_win.HotkeyHook).

Needs the Accessibility permission (System Settings → Privacy & Security → Accessibility) for the app that
runs Python (Terminal / iTerm / the launcher). An active (not listen-only) tap is used so that a dedicated
non-modifier hotkey (F13–F20) can be swallowed.

Modifier keys arrive as kCGEventFlagsChanged; up/down is read from the device-dependent flag bits, which also
tell left and right apart. Events this app posts itself (Cmd+V, typed text) carry a marker and are ignored.

UNTESTED ON REAL HARDWARE: written against the Quartz API; please report issues.
"""
from __future__ import annotations

import threading

import Quartz

# macOS virtual key codes (Carbon kVK_*)
GROUPS = {
    "right_cmd": {54}, "left_cmd": {55}, "cmd": {54, 55},
    "right_option": {61}, "left_option": {58}, "option": {58, 61},
    "right_ctrl": {62}, "left_ctrl": {59}, "ctrl": {59, 62},
    "right_shift": {60}, "shift": {56, 60}, "fn": {63}, "caps_lock": {57}, "space": {49},
    "f13": {105}, "f14": {107}, "f15": {113}, "f16": {106}, "f17": {64}, "f18": {79}, "f19": {80}, "f20": {90},
}
MODIFIERS = {54, 55, 56, 58, 59, 60, 61, 62, 63}
ESC = 53
# device-dependent modifier bits (IOLLEvent.h NX_DEVICE*KEYMASK) + fn / caps
_MOD_MASK = {54: 0x10, 55: 0x08, 56: 0x02, 60: 0x04, 58: 0x20, 61: 0x40, 59: 0x01, 62: 0x2000,
             63: 0x800000, 57: 0x10000}
INJECT_MARK = 0x4C44  # "LD": set on events we post (inject_mac), skipped here

KEY_LABELS = {"right_option": "Right ⌥ Option", "right_cmd": "Right ⌘ Command", "fn": "fn 🌐",
              "ctrl+option": "Ctrl + ⌥", "f13": "F13"}
VK_NAMES = {54: "right_cmd", 55: "left_cmd", 56: "shift", 60: "right_shift", 58: "left_option", 61: "right_option",
            59: "left_ctrl", 62: "right_ctrl", 63: "fn", 57: "caps_lock", 49: "space",
            105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18", 80: "f19", 90: "f20"}
LABELS = {"right_cmd": "Right ⌘", "left_cmd": "⌘", "cmd": "⌘", "right_option": "Right ⌥", "left_option": "⌥",
          "option": "⌥", "right_ctrl": "Right Ctrl", "left_ctrl": "Ctrl", "ctrl": "Ctrl", "shift": "Shift",
          "right_shift": "Right Shift", "fn": "fn 🌐", "caps_lock": "Caps Lock", "space": "Space"}
DIALOG_HINT = "מתאים: Right ⌥ Option, Right ⌘ Command, fn, F13–F20, או צירוף כמו Ctrl + ⌥."


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
    for vk in vks:
        if VK_NAMES.get(vk, "").startswith("f1") or VK_NAMES.get(vk, "").startswith("f2"):
            return vk_name(vk)  # a dedicated F-key wins (swallowed, no conflicts)
    order = [59, 62, 58, 61, 55, 54, 56, 60, 63]
    names, seen = [], set()
    for vk in sorted(vks, key=lambda v: order.index(v) if v in order else 99):
        n = vk_name(vk)
        if n not in seen:
            seen.add(n); names.append(n)
    return "+".join(names)


class HotkeyHook:
    def __init__(self, spec: str, on_down, on_up, on_other, on_escape):
        self.on_down, self.on_up, self.on_other, self.on_escape = on_down, on_up, on_other, on_escape
        self.accept_injected = False
        self.capture = None
        self.set_spec(spec)
        self._held: set[int] = set()
        self._active = False
        self._thread = None
        self._loop = None
        self._tap = None

    def set_spec(self, spec: str):
        self.spec = spec
        self.groups = parse(spec)
        self.keys = set().union(*self.groups)
        self.suppress = not (self.keys & MODIFIERS)

    # ------------------------------------------------------------------ tap callback
    def _callback(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)  # macOS disables slow taps; turn it back on
            return event
        if not self.accept_injected and \
                Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == INJECT_MARK:
            return event
        vk = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        if etype == Quartz.kCGEventFlagsChanged:
            mask = _MOD_MASK.get(vk)
            if mask is None:
                return event
            down = bool(Quartz.CGEventGetFlags(event) & mask)
        else:
            down = etype == Quartz.kCGEventKeyDown
            if down and Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                return None if (vk in self.keys and self.suppress) else event
        if self.capture is not None:
            try:
                self.capture(vk, down)
            except Exception:
                pass
            return event
        ours = vk in self.keys
        try:
            if down:
                if vk == ESC:
                    self.on_escape()
                was = vk in self._held
                self._held.add(vk)
                if not was:
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
        if ours and self.suppress and etype != Quartz.kCGEventFlagsChanged:
            return None
        return event

    # ------------------------------------------------------------------ lifecycle
    def _run(self):
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp) |
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        self._tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                            Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
        if self._tap is None:
            raise PermissionError("Could not create the keyboard event tap: grant Accessibility (and Input "
                                  "Monitoring) to the app running Python, then restart.")
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()
        Quartz.CGEventTapEnable(self._tap, False)
        self._tap = None

    def start(self):
        self._held.clear(); self._active = False
        self._thread = threading.Thread(target=self._run, name="hotkey-tap", daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop is not None:
            Quartz.CFRunLoopStop(self._loop)
        if self._thread:
            self._thread.join(1.0)
        self._thread, self._loop = None, None

    def restart(self):
        self.stop(); self.start()
