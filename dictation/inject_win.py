"""Insert text at the caret of the focused app without changing focus and without pressing Enter.

paste (default): put text on the clipboard (excluded from Win+V history / cloud clipboard), send Ctrl+V,
                 then restore the previous clipboard contents if nothing else touched the clipboard.
                 Most reliable for Hebrew in Chrome/Electron/WhatsApp/VS Code/terminals.
type:            SendInput KEYEVENTF_UNICODE per character; never touches the clipboard. Newlines are
                 sent as spaces so no message is ever submitted.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x2, 0x4
VK_CONTROL, VK_V = 0x11, 0x56
# Clipboard formats that are GDI handles (not HGLOBAL) — cannot be byte-copied; Windows synthesizes them.
_NON_HGLOBAL = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}

kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class InjectError(RuntimeError):
    pass


def _open_clipboard(tries=20):
    for _ in range(tries):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.015)
    raise InjectError("clipboard busy")


def _hglobal(data: bytes):
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    return h


def _snapshot() -> list[tuple[int, bytes]]:
    _open_clipboard()
    saved, fmt = [], 0
    try:
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _NON_HGLOBAL:
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            size = kernel32.GlobalSize(h)
            p = kernel32.GlobalLock(h)
            if p and size:
                saved.append((fmt, ctypes.string_at(p, size)))
                kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
    return saved


def _set(items: list[tuple[int, bytes]], private: bool):
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        for fmt, data in items:
            user32.SetClipboardData(fmt, _hglobal(data))
        if private:  # keep dictated text out of clipboard history, cloud sync and clipboard managers
            zero = (0).to_bytes(4, "little")
            for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                user32.SetClipboardData(user32.RegisterClipboardFormatW(name), _hglobal(zero))
            user32.SetClipboardData(user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), _hglobal(b"\0"))
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str, private=False):
    _set([(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))], private)


def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise InjectError("SendInput blocked (target may run elevated)")


def _key(vk, up=False, scan=0, unicode=False):
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_UNICODE if unicode else 0)
    return INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


def paste(text: str, restore=True):
    saved = _snapshot() if restore else None
    set_clipboard_text(text, private=True)
    seq = user32.GetClipboardSequenceNumber()
    _send([_key(VK_CONTROL), _key(VK_V), _key(VK_V, up=True), _key(VK_CONTROL, up=True)])
    if restore:
        time.sleep(0.35)  # target reads the clipboard asynchronously after Ctrl+V
        if user32.GetClipboardSequenceNumber() == seq:  # nobody else changed it meanwhile
            if saved:
                _set(saved, private=False)
            else:
                _open_clipboard(); user32.EmptyClipboard(); user32.CloseClipboard()


def type_text(text: str):
    text = text.replace("\r", "").replace("\n", " ")
    units = text.encode("utf-16-le")
    inputs = []
    for i in range(0, len(units), 2):
        cu = int.from_bytes(units[i:i + 2], "little")
        inputs += [_key(0, scan=cu, unicode=True), _key(0, up=True, scan=cu, unicode=True)]
    for i in range(0, len(inputs), 200):
        _send(inputs[i:i + 200])
        time.sleep(0.005)


def insert(text: str, method="paste", restore=True):
    if method == "type":
        type_text(text)
    else:
        paste(text, restore)
