"""Тесты путей данных (репозиторий и упакованная сборка)."""

import sys

import pytest

from polytopia_analyzer import paths


def test_env_override_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYTOPIA_STATE_DIR", str(tmp_path / "state"))
    assert paths.user_state_dir() == tmp_path / "state"
    assert paths.inbox_dir() == tmp_path / "state" / "inbox"
    assert paths.shim_bin_dir() == tmp_path / "state" / "bin"


def test_env_override_data_and_inbox(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYTOPIA_DATA_DIR", str(tmp_path / "matches"))
    monkeypatch.setenv("POLYREAD_DUMP_DIR", str(tmp_path / "inbox"))
    assert paths.matches_dir() == tmp_path / "matches"
    assert paths.inbox_dir() == tmp_path / "inbox"


def test_frozen_build_uses_user_state(monkeypatch, tmp_path):
    """В onefile-сборке данные не могут лежать рядом с кодом (он во временной папке)."""
    monkeypatch.delenv("POLYTOPIA_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "_home", lambda: tmp_path)
    assert paths.matches_dir() == tmp_path / ".local/share/polytopia-analyzer/matches"


def test_repo_checkout_uses_project_data(monkeypatch):
    monkeypatch.delenv("POLYTOPIA_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.matches_dir().name == "data"


def test_default_state_dir_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("POLYTOPIA_STATE_DIR", raising=False)
    monkeypatch.setattr(paths, "_home", lambda: tmp_path)
    st = paths.user_state_dir()
    assert st == tmp_path / ".local/share/polytopia-analyzer"
    assert paths.probe_dir() == st / "probe"