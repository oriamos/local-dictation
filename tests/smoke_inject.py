"""Smoke test for text injection, in a self-contained test window (never touches the user's apps).

Checks: paste of Hebrew+English+symbols lands at the caret, typed (SendInput Unicode) text lands,
previous clipboard is restored, UI Automation reports the password field as a password field,
and the foreground process detection works.
Run:  python -m tests.smoke_inject
"""
import ctypes
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QVBoxLayout, QWidget  # noqa: E402

from dictation import context, inject  # noqa: E402

TEXT = "פתח את ה-repository ב-GitHub ותבדוק את ה-migrations ב-Supabase. 1,250 ₪ "
results = {}


class Main(QObject):  # marshals calls from the worker thread onto the Qt thread
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda fn: fn())


def worker(app, w, edit, pwd, main):
    def QTimer_singleShot(_ms, fn):
        main.call.emit(fn)

    class QTimer:
        singleShot = staticmethod(QTimer_singleShot)

    time.sleep(1.0)
    fg = context.foreground_hwnd()
    results["fg"] = context.process_name(fg)
    if fg != int(w.winId()):  # never inject into someone else's window
        results["fg"] += " (NOT the test window — aborted, nothing injected)"
        QTimer.singleShot(0, app.quit); return
    results["pwd_on_text"] = context.focused_is_password()
    t0 = time.perf_counter(); inject.insert(TEXT, "paste"); results["paste_ms"] = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter(); inject.insert("+typed עברית", "type"); results["type_ms"] = (time.perf_counter() - t0) * 1000
    time.sleep(0.3)
    QTimer.singleShot(0, pwd.setFocus)
    time.sleep(0.4)
    results["pwd_on_password"] = context.focused_is_password()
    time.sleep(0.1)
    QTimer.singleShot(0, app.quit)


def main():
    users_clipboard = inject._snapshot()  # put back at the end
    try:
        run()
    finally:
        if users_clipboard:
            inject._set(users_clipboard, private=False)


def run():
    context.warm_uia()
    inject.set_clipboard_text("CLIPBOARD-BEFORE")
    app = QApplication(sys.argv)
    w = QWidget(); w.setWindowTitle("LocalDictation injection test"); w.resize(600, 200)
    lay = QVBoxLayout(w); edit = QTextEdit(); pwd = QLineEdit(); pwd.setEchoMode(QLineEdit.Password)
    lay.addWidget(edit); lay.addWidget(pwd)
    w.show(); w.raise_(); w.activateWindow()
    # Test-only: Windows' foreground lock refuses SetForegroundWindow from a background process
    # unless input was just received; a synthetic Alt tap satisfies that rule.
    u32 = ctypes.windll.user32
    u32.keybd_event(0x12, 0, 0, 0); u32.keybd_event(0x12, 0, 2, 0)
    u32.SetForegroundWindow(int(w.winId()))
    edit.setFocus()
    main = Main()
    threading.Thread(target=worker, args=(app, w, edit, pwd, main), daemon=True).start()
    app.exec()
    got = edit.toPlainText()
    clip = None
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    k.GlobalLock.restype = ctypes.c_void_p
    u.GetClipboardData.restype = ctypes.c_void_p
    if u.OpenClipboard(None):
        h = u.GetClipboardData(13)
        clip = ctypes.wstring_at(k.GlobalLock(ctypes.c_void_p(h))) if h else None
        u.CloseClipboard()
    print("foreground process :", results.get("fg"))
    print("text field         :", repr(got))
    print("password? text/pwd :", results.get("pwd_on_text"), "/", results.get("pwd_on_password"))
    print(f"paste {results.get('paste_ms', 0):.0f} ms (incl. 350 ms clipboard-restore wait) | type {results.get('type_ms', 0):.0f} ms")
    print("clipboard after    :", repr(clip))
    checks = {"paste": TEXT.strip() in got, "type": "+typed עברית" in got, "restore": clip == "CLIPBOARD-BEFORE",
              "pwd_detect": results.get("pwd_on_password") is True and results.get("pwd_on_text") is False}
    print("RESULT:", checks, "PASS" if all(checks.values()) else "FAIL")


if __name__ == "__main__":
    main()
