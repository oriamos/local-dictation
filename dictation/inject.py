"""Text injection at the caret: platform dispatcher. Same API on both (insert, set_clipboard_text, InjectError)."""
import sys

if sys.platform == "darwin":
    from .inject_mac import *  # noqa: F401,F403
    from .inject_mac import _set, _snapshot  # noqa: F401
else:
    from .inject_win import *  # noqa: F401,F403
    from .inject_win import _set, _snapshot  # noqa: F401
