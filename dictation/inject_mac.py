"""Insert text at the caret on macOS without changing focus and without pressing Return (same API as inject_win).

paste (default): NSPasteboard + Cmd+V, previous clipboard restored if nothing else changed it. The dictated text is
                 marked transient/concealed (nspasteboard.org conventions) so clipboard managers skip it.
type:            CGEventKeyboardSetUnicodeString, never touches the clipboard; newlines become spaces.

Requires Accessibility permission to post keyboard events. Refuses while macOS Secure Input is on (password
fields, some terminals), which would silently drop the keystrokes anyway.

UNTESTED ON REAL HARDWARE: written against AppKit/Quartz; please report issues.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

from .hotkey_mac import INJECT_MARK

KVK_V = 9
TRANSIENT = "org.nspasteboard.TransientType"
CONCEALED = "org.nspasteboard.ConcealedType"

_carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))
_carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool


class InjectError(RuntimeError):
    pass


def secure_input_on() -> bool:
    try:
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False


def _snapshot():
    pb = NSPasteboard.generalPasteboard()
    saved = []
    for item in pb.pasteboardItems() or []:
        d = {}
        for t in item.types():
            data = item.dataForType_(t)
            if data is not None:
                d[str(t)] = data
        saved.append(d)
    return saved


def _set(items, private: bool = False):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    objs = []
    for d in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in d.items():
            it.setData_forType_(data, t)
        objs.append(it)
    if objs:
        pb.writeObjects_(objs)


def set_clipboard_text(text: str, private: bool = False):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    types = [NSPasteboardTypeString] + ([TRANSIENT, CONCEALED] if private else [])
    pb.declareTypes_owner_(types, None)
    pb.setString_forType_(text, NSPasteboardTypeString)
    if private:
        pb.setString_forType_("", TRANSIENT); pb.setString_forType_("", CONCEALED)


def _post(ev):
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGEventSourceUserData, INJECT_MARK)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _cmd_v():
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, KVK_V, down)
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        _post(ev)


def paste(text: str, restore=True):
    if secure_input_on():
        raise InjectError("macOS Secure Input is active")
    pb = NSPasteboard.generalPasteboard()
    saved = _snapshot() if restore else None
    set_clipboard_text(text, private=True)
    count = pb.changeCount()
    _cmd_v()
    if restore:
        time.sleep(0.35)
        if pb.changeCount() == count:
            if saved:
                _set(saved)
            else:
                pb.clearContents()


def type_text(text: str):
    if secure_input_on():
        raise InjectError("macOS Secure Input is active")
    text = text.replace("\r", "").replace("\n", " ")
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for i in range(0, len(text), 20):  # the API accepts at most ~20 UTF-16 units per event
        chunk = text[i:i + 20]
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(src, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
            _post(ev)
        time.sleep(0.005)


def insert(text: str, method="paste", restore=True):
    if method == "type":
        type_text(text)
    else:
        paste(text, restore)
