"""Shared pytest fixtures.

Every test runs against a throwaway ``data_dir`` so the suite never reads or
writes the real notes database, config, or backups.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the repo root importable so ``import server...`` works under CI.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point the global settings at a per-test temp data dir.

    ``data_path``/``db_path``/``public_url_file``/backup dir are all derived from
    ``data_dir`` on each access, so this one patch isolates everything.
    """
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    yield
