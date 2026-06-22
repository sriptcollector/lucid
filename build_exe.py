"""Build the standalone desktop app (Lucid) with PyInstaller.

Cross-platform: produces a Windows folder app (Lucid.exe), a macOS .app bundle
(Lucid.app), or a Linux folder app — depending on the OS it runs ON. PyInstaller
cannot cross-compile, so each OS is built on its own machine (locally, or via the
GitHub Actions workflow in .github/workflows/build-apps.yml for macOS).

Run from the repo root inside a venv with deps installed:

    python -m pip install -r requirements.txt "pyinstaller>=6"
    python build_exe.py

Notes:
  * Entry point lucid_app.py: per-user data dir, localhost server, opens browser.
  * Voice-ID (resemblyzer -> torch, ~528 MB) is EXCLUDED to stay lean; it
    degrades gracefully. faster-whisper / ctranslate2 / PyAV / onnxruntime ARE
    bundled, so local transcription works (the model downloads on first use).
  * --windowed = no console (logs go to the per-user data dir's lucid.log).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001
        return False


def _args() -> list[str]:
    # Always-bundled (core server + local transcription stack).
    collect_all = [
        "faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers",
        "huggingface_hub", "uvicorn", "anthropic",
    ]
    # Optional cloud-transcription SDKs — only if installed in this env.
    for opt in ("openai", "deepgram"):
        if _have(opt):
            collect_all.append(opt)

    args = ["--noconfirm", "--windowed", "--name", "Lucid",
            "--add-data", f"web{os.pathsep}web",
            "--collect-submodules", "server"]
    for pkg in collect_all:
        args += ["--collect-all", pkg]
    for pkg in ("httpx", "httpcore", "anyio"):
        args += ["--collect-submodules", pkg]
    for pkg in ("torch", "resemblyzer", "server.pipeline.voiceid",
                "matplotlib", "tkinter", "PyQt5", "PySide6"):
        args += ["--exclude-module", pkg]
    args.append("lucid_app.py")
    return args


def _output() -> Path:
    if sys.platform == "darwin":
        return ROOT / "dist" / "Lucid.app"
    if sys.platform.startswith("win"):
        return ROOT / "dist" / "Lucid" / "Lucid.exe"
    return ROOT / "dist" / "Lucid" / "Lucid"


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("PyInstaller is not installed. Run:  python -m pip install 'pyinstaller>=6'")
        return 1

    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(ROOT / "dist" / "Lucid", ignore_errors=True)
    shutil.rmtree(ROOT / "dist" / "Lucid.app", ignore_errors=True)
    spec = ROOT / "Lucid.spec"
    if spec.exists():
        spec.unlink()

    pyi.run(_args())

    out = _output()
    if not out.exists():
        print(f"Build finished but the expected output is missing: {out}")
        return 1
    print(f"\nBuilt {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
