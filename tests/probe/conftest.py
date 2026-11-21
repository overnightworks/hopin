from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

SENTINEL = "sentinel-text-that-must-never-be-printed"
NOT_REACHED = [f"check={check} verdict=no reason=not_reached" for check in "ABC"]


@pytest.fixture
def probe_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A complete configuration of the probe in the environment; the answer is its password file."""
    password_file = tmp_path / "password"
    password_file.write_text(f"{SENTINEL}\n")
    for name, value in {
        "ORIGIN": "https://hopin.test.invalid",
        "USERNAME": "probe-user",
        "PASSWORD_FILE": str(password_file),
        "AGENTS": '["Darwin", "Newton"]',
        "VOICE_FILE": str(tmp_path / "missing-voice.onnx"),
    }.items():
        monkeypatch.setenv(f"HOPIN_PROBE_{name}", value)
    return password_file
