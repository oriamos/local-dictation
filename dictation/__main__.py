import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # models are local; no network at runtime
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from .app import main  # noqa: E402

sys.exit(main())
