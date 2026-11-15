"""Simulated macOS hotkey test (runs on any OS): stubs the Quartz module and drives HotkeyHook._callback
with fake events. Verifies the state machine, left/right modifier detection via device flag bits,
F-key suppression and skipping of our own injected events. It does NOT verify the real Quartz API.
Run:  python -m tests.sim_mac_hotkey
"""
import sys
import types

Q = types.ModuleType("Quartz")
for i, n in enumerate(["kCGEventKeyDown", "kCGEventKeyUp", "kCGEventFlagsChanged", "kCGEventTapDisabledByTimeout",
                       "kCGEventTapDisabledByUserInput", "kCGEventSourceUserData", "kCGKeyboardEventKeycode",
                       "kCGKeyboardEventAutorepeat"]):
    setattr(Q, n, 100 + i)
Q.kCGEventKeyDown, Q.kCGEventKeyUp, Q.kCGEventFlagsChanged = 10, 11, 12


class Ev:
    def __init__(self, vk, flags=0, mark=0, repeat=0):
        self.vk, self.flags, self.mark, self.repeat = vk, flags, mark, repeat


def field(ev, f):
    return {Q.kCGKeyboardEventKeycode: ev.vk, Q.kCGEventSourceUserData: ev.mark,
            Q.kCGKeyboardEventAutorepeat: ev.repeat}[f]


Q.CGEventGetIntegerValueField = field
Q.CGEventGetFlags = lambda ev: ev.flags
Q.CGEventTapEnable = lambda *a: None
sys.modules["Quartz"] = Q

from dictation import hotkey_mac as hm  # noqa: E402

RCMD, LCMD, ROPT, SHIFT, F13, ESC = 54, 55, 61, 56, 105, 53
ROPT_BIT, LCMD_BIT, RCMD_BIT, SHIFT_BIT = 0x40, 0x08, 0x10, 0x02


def run(spec, seq):
    ev = []
    h = hm.HotkeyHook(spec, lambda: ev.append("down"), lambda: ev.append("up"),
                      lambda: ev.append("other"), lambda: ev.append("esc"))
    swallowed = []
    for etype, e in seq:
        r = h._callback(None, etype, e, None)
        if r is None:
            swallowed.append(e.vk)
    return ev, swallowed


FC, KD, KU = Q.kCGEventFlagsChanged, Q.kCGEventKeyDown, Q.kCGEventKeyUp
tests = {
    "right option hold/release":
        (run("right_option", [(FC, Ev(ROPT, ROPT_BIT)), (FC, Ev(ROPT, 0))]), (["down", "up"], [])),
    "left cmd does not trigger right cmd":
        (run("right_cmd", [(FC, Ev(LCMD, LCMD_BIT)), (FC, Ev(LCMD, 0))]), ([], [])),
    "right cmd while left cmd held":
        (run("right_cmd", [(FC, Ev(LCMD, LCMD_BIT)), (FC, Ev(RCMD, LCMD_BIT | RCMD_BIT)), (FC, Ev(RCMD, LCMD_BIT)),
                           (FC, Ev(LCMD, 0))]), (["down", "up"], [])),
    "right option + key = shortcut":
        (run("right_option", [(FC, Ev(ROPT, ROPT_BIT)), (KD, Ev(0)), (KU, Ev(0)), (FC, Ev(ROPT, 0))]),
         (["down", "other", "up"], [])),
    "esc while held":
        (run("right_option", [(FC, Ev(ROPT, ROPT_BIT)), (KD, Ev(ESC)), (KU, Ev(ESC)), (FC, Ev(ROPT, 0))]),
         (["down", "esc", "other", "up"], [])),
    "F13 swallowed":
        (run("f13", [(KD, Ev(F13)), (KD, Ev(F13, repeat=1)), (KU, Ev(F13))]), (["down", "up"], [F13, F13, F13])),
    "own injected Cmd+V ignored":
        (run("right_cmd", [(FC, Ev(RCMD, RCMD_BIT, mark=hm.INJECT_MARK)), (FC, Ev(RCMD, 0, mark=hm.INJECT_MARK))]),
         ([], [])),
    "combo ctrl+option":
        (run("ctrl+option", [(FC, Ev(59, 0x01)), (FC, Ev(58, 0x01 | 0x20)), (FC, Ev(58, 0x01)), (FC, Ev(59, 0))]),
         (["down", "up"], [])),
}
ok = True
for name, (got, want) in tests.items():
    good = got == want
    ok &= good
    print(f"{'PASS' if good else 'FAIL'}  {name}: {got}" + ("" if good else f" (want {want})"))
for vks, want in {(55, 56, 105): "f13", (61,): "right_option", (59, 58): "left_ctrl+left_option", (63,): "fn"}.items():
    got = hm.spec_from_combo(list(vks))
    ok &= got == want
    print(f"{'PASS' if got == want else 'FAIL'}  spec_from_combo{vks} -> {got} ({hm.label(got)})")
print("RESULT:", "PASS" if ok else "FAIL")
