"""Build the standalone Windows app (Lucid.exe) with PyInstaller.

Run from the repo root inside the project's virtualenv:

    .venv\\Scripts\\python.exe -m pip install "pyinstaller>=6"
    .venv\\Scripts\\python.exe build_exe.py

Output: dist/Lucid/Lucid.exe (+ _internal/). Zip the dist/Lucid folder to ship.

Notes:
  * The entry point is lucid_app.py (sets a per-user data dir, then runs the
    server bound to localhost and opens the browser).
  * Voice-ID (resemblyzer -> torch, ~528 MB) is EXCLUDED so the build stays
    lean (~450 MB folder / ~180 MB zip). Voice features degrade gracefully.
  * faster-whisper / ctranslate2 / PyAV / onnxruntime ARE bundled, so local
    transcription works with no extra install; the model downloads on first use.
  * --windowed = no console window (logs go to %LOCALAPPDATA%/Lucid/lucid.log).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ARGS = [
    "--noconfirm", "--windowed", "--name", "Lucid",
    "--add-data", "web;web",
    "--collect-submodules", "server",
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "av",
    "--collect-all", "onnxruntime",
    "--collect-all", "tokenizers",
    "--collect-all", "huggingface_hub",
    "--collect-all", "uvicorn",
    "--collect-all", "anthropic",
    "--collect-all", "openai",
    "--collect-submodules", "httpx",
    "--collect-submodules", "httpcore",
    "--collect-submodules", "anyio",
    "--exclude-module", "torch",
    "--exclude-module", "resemblyzer",
    "--exclude-module", "server.pipeline.voiceid",
    "--exclude-module", "matplotlib",
    "--exclude-module", "tkinter",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide6",
    "lucid_app.py",
]


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("PyInstaller is not installed. Run:  python -m pip install 'pyinstaller>=6'")
        return 1

    for d in ("build", "dist/Lucid"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    spec = ROOT / "Lucid.spec"
    if spec.exists():
        spec.unlink()

    pyi.run(ARGS)
    exe = ROOT / "dist" / "Lucid" / ("Lucid.exe" if sys.platform.startswith("win") else "Lucid")
    if not exe.exists():
        print("Build finished but the executable is missing — check the log above.")
        return 1
    print(f"\nBuilt {exe}")
    print("Zip the dist/Lucid folder (top-level folder 'Lucid') to distribute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
