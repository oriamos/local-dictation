"""Global hotkey: platform dispatcher (Windows low-level hook / macOS Quartz event tap). Same API on both."""
import sys

if sys.platform == "darwin":
    from .hotkey_mac import *  # noqa: F401,F403
    from .hotkey_mac import DIALOG_HINT, KEY_LABELS, MODIFIERS, VK_NAMES, label, spec_from_combo  # noqa: F401
else:
    from .hotkey_win import *  # noqa: F401,F403
    from .hotkey_win import DIALOG_HINT, KEY_LABELS, MODIFIERS, VK_NAMES, label, spec_from_combo  # noqa: F401
