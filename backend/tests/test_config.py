"""Configuration defaults are loopback-only; env overrides work."""

import os

import pytest

from app.config import Settings


def test_defaults_bind_loopback() -> None:
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.max_page_size == 100


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SPOOFSKY_PORT", "8123")
    monkeypatch.setenv("SPOOFSKY_DATABASE_PATH", str(tmp_path / "custom.db"))
    settings = Settings.from_env()
    assert settings.port == 8123
    assert settings.database_path == tmp_path / "custom.db"
    assert settings.host == "127.0.0.1"  # host has no override in this test
    assert os.environ["SPOOFSKY_PORT"] == "8123"
