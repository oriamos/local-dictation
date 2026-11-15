"""Install / update the Lecture Transcription plugin in ONLYOFFICE Desktop Editors and point it at this app.

The plugin folder (named after the plugin GUID) goes into the user-level sdkjs-plugins directory, so no admin
rights are needed. settings.js (local port + access token) is rewritten on every app start.
ONLYOFFICE must be restarted once after the first install.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "onlyoffice-plugin"
GUID = json.loads((SRC / "config.json").read_text(encoding="utf-8"))["guid"]          # asc.{...}
FOLDER = GUID.replace("asc.", "")                                                  # {...}


def user_plugin_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "asc.onlyoffice.ONLYOFFICE" / "data" / "sdkjs-plugins"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ONLYOFFICE" / "DesktopEditors" / "data" / "sdkjs-plugins"


def install_dirs() -> list[Path]:
    """Where the editor itself is installed (to borrow the plugin SDK, v1/plugins.js)."""
    if sys.platform == "darwin":
        return [Path("/Applications/ONLYOFFICE.app/Contents/Resources/editors/sdkjs-plugins")]
    out = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            sub = "Programs/ONLYOFFICE/DesktopEditors" if env == "LOCALAPPDATA" else "ONLYOFFICE/DesktopEditors"
            out.append(Path(base) / sub / "editors" / "sdkjs-plugins")
    return out


def editor_installed() -> bool:
    return any(d.exists() for d in install_dirs()) or user_plugin_dir().parent.exists()


def target() -> Path:
    return user_plugin_dir() / FOLDER


def installed() -> bool:
    return (target() / "config.json").exists()


def write_settings(port: int, token: str, dest: Path | None = None):
    dest = dest or target()
    if dest.exists():
        (dest / "settings.js").write_text(
            "window.LD_CONFIG = " + json.dumps({"port": port, "token": token}) + ";\n", encoding="utf-8")


def install(port: int, token: str) -> Path:
    dest = target()
    dest.mkdir(parents=True, exist_ok=True)
    for f in SRC.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    # Offline fallback for the plugin SDK: copy it from the local editor install if present.
    for d in install_dirs():
        sdk = d / "v1" / "plugins.js"
        if sdk.exists():
            shutil.copy2(sdk, dest / "plugins.js")
            break
    write_settings(port, token, dest)
    return dest
