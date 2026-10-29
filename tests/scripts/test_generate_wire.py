"""Tests for the generator that writes the browser's typed wire vocabulary."""

from typing import TYPE_CHECKING

import pytest

from scripts import generate_wire

if TYPE_CHECKING:
    from pathlib import Path


def test_main_check_committed_wire_module_exits_clean() -> None:
    assert generate_wire.main(["--check"]) == 0


def test_render_core_vocabulary_names_enums_and_constants() -> None:
    rendered = generate_wire.render()

    assert 'export enum SIGNAL_TYPE {\n    OFFER = "offer",' in rendered
    assert "export enum ERROR_REASON {" in rendered
    assert "export enum ROOM_CLOSED_REASON {" in rendered
    assert 'export const WEBSOCKET_PATH = "/ws";' in rendered
    assert "export const SESSION_ENDED_CLOSE_CODE = 4002;" in rendered
    assert "export const REPLAY_LOG_LIMIT = 256;" in rendered
    description_types = "export const DESCRIPTION_TYPES: readonly SIGNAL_TYPE[]"
    assert f"{description_types} = [SIGNAL_TYPE.ANSWER, SIGNAL_TYPE.OFFER];" in rendered
    assert "const" + " enum" not in rendered


def test_main_without_check_writes_the_rendered_module(tmp_path: Path) -> None:
    output = tmp_path / "wire.ts"

    assert generate_wire.main([], output) == 0
    assert output.read_text() == generate_wire.render()


def test_main_check_in_sync_file_exits_clean(tmp_path: Path) -> None:
    output = tmp_path / "wire.ts"
    output.write_text(generate_wire.render())

    assert generate_wire.main(["--check"], output) == 0


def test_main_check_drifted_file_exits_with_message(tmp_path: Path) -> None:
    output = tmp_path / "wire.ts"
    output.write_text(generate_wire.render() + "// hand edit\n")

    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], output)
