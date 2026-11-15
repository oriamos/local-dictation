"""Foreground-app context and focused-field checks.

Privacy: only the process name of the foreground window is read, never window titles or content.
The UI Automation query reads a single boolean (IsPassword) of the focused element.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)


def foreground_hwnd() -> int:
    return user32.GetForegroundWindow() or 0


def window_alive(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def process_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        name = psutil.Process(pid.value).name().lower()
    except Exception:
        return ""
    return name[:-4] if name.endswith(".exe") else name


def mode_for(proc: str, profiles: dict[str, str], default: str) -> str:
    return profiles.get(proc, default)


_uia = None


def _focused_is_password() -> bool | None:
    global _uia
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        if _uia is None:
            comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        _uia = True
        el = uia.GetFocusedElement()
        return bool(el.CurrentIsPassword) if el else None
    finally:
        comtypes.CoUninitialize()


def warm_uia():
    """Generate the COM wrappers once at startup (first use takes seconds)."""
    try:
        _focused_is_password()
    except Exception:
        pass


def focused_is_password(timeout=0.35) -> bool:
    """True only if UI Automation positively reports a password field. Runs on a short-lived
    thread with a timeout so a hung target app can never freeze dictation."""
    out: list = [None]

    def work():
        try:
            out[0] = _focused_is_password()
        except Exception:
            out[0] = None

    t = threading.Thread(target=work, daemon=True)
    t.start(); t.join(timeout)
    return out[0] is True


def missing_permissions() -> list[str]:
    return []  # Windows needs no special grants (mic access is prompted by the OS on first use)
