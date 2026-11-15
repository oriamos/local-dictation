"""Start Local Dictation without a console window (double-click, or used by "Start with Windows").
Runs from any current directory: the project folder is put on sys.path explicitly.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
# Double-clicking a .pyw uses the system Python; hand over to the project's environment.
if VENV_PYW.exists() and Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
    import subprocess
    subprocess.Popen([str(VENV_PYW), str(Path(__file__).resolve())], cwd=str(ROOT))
    sys.exit(0)
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
# Models are already on disk: never contact Hugging Face at runtime (privacy; also works fully offline).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from dictation.app import main  # noqa: E402

sys.exit(main())
