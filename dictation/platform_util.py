"""OS integration that is not per-keystroke: single instance, app preparation, start at login."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = sys.platform == "darwin"
_lock_handle = None


def _launch_cmd() -> list[str]:
    if MAC:
        py = Path(sys.executable)
    else:
        py = Path(sys.executable).with_name("pythonw.exe")
    return [str(py), str(ROOT / "launch.pyw")]


# ---------------------------------------------------------------------- single instance
def single_instance() -> bool:
    """True if this is the only running instance."""
    global _lock_handle
    if MAC:
        import fcntl
        from .storage import APP_DIR
        APP_DIR.mkdir(parents=True, exist_ok=True)
        _lock_handle = open(APP_DIR / "app.lock", "w")
        try:
            fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    import ctypes
    _lock_handle = ctypes.windll.kernel32.CreateMutexW(None, False, "LocalDictation.SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def prepare_process():
    """Before the QApplication exists."""
    if not MAC:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def prepare_app():
    """After the QApplication exists. macOS: menu-bar only (no Dock icon, never steals focus)."""
    if MAC:
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception:
            pass


# ---------------------------------------------------------------------- start at login
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_PLIST = Path.home() / "Library" / "LaunchAgents" / "ai.localdictation.plist"


def autostart_enabled() -> bool:
    if MAC:
        return _PLIST.exists()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, "LocalDictation")[0])
    except OSError:
        return False


def set_autostart(on: bool):
    if MAC:
        if on:
            import plistlib
            _PLIST.parent.mkdir(parents=True, exist_ok=True)
            with open(_PLIST, "wb") as f:
                plistlib.dump({"Label": "ai.localdictation", "ProgramArguments": _launch_cmd(),
                               "WorkingDirectory": str(ROOT), "RunAtLoad": True,
                               "EnvironmentVariables": {"HF_HUB_OFFLINE": "1"}}, f)
        elif _PLIST.exists():
            _PLIST.unlink()
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            cmd = " ".join(f'"{p}"' for p in _launch_cmd())
            winreg.SetValueEx(k, "LocalDictation", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "LocalDictation")
            except OSError:
                pass
