"""Foreground-app context and password-field detection: platform dispatcher.

API: foreground_hwnd() -> opaque id of the frontmost window/app, window_alive(id), process_name(id),
     mode_for(proc, profiles, default), focused_is_password(), warm_uia(), missing_permissions() -> list[str]
"""
import sys

if sys.platform == "darwin":
    from .context_mac import *  # noqa: F401,F403
else:
    from .context_win import *  # noqa: F401,F403
