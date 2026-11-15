"""Hook logic test with synthetic keys: press/release, shortcut detection, Esc, combo, key repeat.
Uses only modifier keys + Esc, which type nothing into whatever window is in front.
Run:  python -m tests.smoke_hotkey
"""
import ctypes
import time

from dictation.hotkey import HotkeyHook

u = ctypes.windll.user32
ev = []


def key(vk, up=False):
    u.keybd_event(vk, 0, 2 if up else 0 | 1, 0)  # 1 = extended (needed for Right Ctrl)
    time.sleep(0.05)


def run(spec, seq):
    ev.clear()
    h = HotkeyHook(spec, lambda: ev.append("down"), lambda: ev.append("up"),
                   lambda: ev.append("other"), lambda: ev.append("esc"))
    h.accept_injected = True
    h.start(); time.sleep(0.3)
    for vk, up in seq:
        u.keybd_event(vk, 0, (2 if up else 0) | (1 if vk in (0xA3, 0xA5) else 0), 0)
        time.sleep(0.06)
    time.sleep(0.2); h.stop()
    return list(ev)


RC, LC, LWIN, SHIFT, ESC = 0xA3, 0xA2, 0x5B, 0xA0, 0x1B
tests = {
    "right_ctrl hold/release": (run("right_ctrl", [(RC, 0), (RC, 0), (RC, 0), (RC, 1)]), ["down", "up"]),
    "right_ctrl + shift = shortcut": (run("right_ctrl", [(RC, 0), (SHIFT, 0), (SHIFT, 1), (RC, 1)]), ["down", "other", "up"]),
    "left ctrl ignored": (run("right_ctrl", [(LC, 0), (LC, 1)]), []),
    "ctrl+shift combo": (run("ctrl+shift", [(LC, 0), (SHIFT, 0), (SHIFT, 1), (LC, 1)]), ["down", "up"]),
}
ok = True
for name, (got, want) in tests.items():
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: {got}" + ("" if got == want else f" (want {want})"))
    ok &= got == want
print("RESULT:", "PASS" if ok else "FAIL")
