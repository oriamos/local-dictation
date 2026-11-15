"""Foreground-app context and password-field detection on macOS (same API as context_win).

Privacy: only the frontmost app's name is read; the Accessibility query reads just the focused element's role.
The "window id" here is the frontmost app's PID.

UNTESTED ON REAL HARDWARE: written against AppKit/ApplicationServices; please report issues.
"""
from __future__ import annotations

import threading

from AppKit import NSRunningApplication, NSWorkspace


def foreground_hwnd() -> int:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app else 0


def window_alive(pid: int) -> bool:
    return bool(pid) and NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) is not None


def process_name(pid: int) -> str:
    if not pid:
        return ""
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return (str(app.localizedName() or "") if app else "").lower()


def mode_for(proc: str, profiles: dict[str, str], default: str) -> str:
    return profiles.get(proc, default)


def _focused_is_password() -> bool | None:
    from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide,
                                     kAXFocusedUIElementAttribute, kAXRoleAttribute, kAXSubroleAttribute)
    err, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute, None)
    if err or el is None:
        return None
    for attr in (kAXSubroleAttribute, kAXRoleAttribute):
        err, v = AXUIElementCopyAttributeValue(el, attr, None)
        if not err and v == "AXSecureTextField":
            return True
    return False


def warm_uia():
    pass  # nothing to pre-generate on macOS


def focused_is_password(timeout=0.35) -> bool:
    from .inject_mac import secure_input_on
    if secure_input_on():  # a password field anywhere has focus-level secure input
        return True
    out = [None]

    def work():
        try:
            out[0] = _focused_is_password()
        except Exception:
            out[0] = None

    t = threading.Thread(target=work, daemon=True)
    t.start(); t.join(timeout)
    return out[0] is True


def missing_permissions(prompt: bool = True) -> list[str]:
    """Accessibility (post keys / read focus), Input Monitoring (global hotkey), Microphone.
    With prompt=True macOS shows its permission dialogs for the ones not yet granted."""
    missing = []
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}):
            missing.append("Accessibility")
    except Exception:
        pass
    try:
        import Quartz
        if not Quartz.CGPreflightListenEventAccess():
            if prompt:
                Quartz.CGRequestListenEventAccess()
            missing.append("Input Monitoring")
    except Exception:
        pass
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        if status == 0 and prompt:  # not determined yet → ask
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda ok: None)
        elif status in (1, 2):      # restricted / denied
            missing.append("Microphone")
    except Exception:
        pass
    return missing
