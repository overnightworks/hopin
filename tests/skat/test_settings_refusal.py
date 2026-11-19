import pytest

from hopin.main import main

pytestmark = pytest.mark.usefixtures("game_environment", "logging_restored")


def test_refuses_a_malformed_skat_origin_in_one_line_naming_it_and_never_its_value(
    sign_in_settings, monkeypatch, capsys
):
    for name, environment_value in sign_in_settings().environment.items():
        monkeypatch.setenv(name, environment_value)
    monkeypatch.setenv("HOPIN_SKAT_ORIGIN", "http://sentinel-skat.test")

    with pytest.raises(SystemExit) as refusal:
        main(["serve"])

    assert refusal.value.code == 1
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "HOPIN_SKAT_ORIGIN is malformed" in lines[0]
    assert "sentinel" not in lines[0]
