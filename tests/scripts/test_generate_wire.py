"""Tests for the generator that writes the browser's typed wire vocabulary."""

from typing import TYPE_CHECKING

import pytest

from scripts import generate_wire

if TYPE_CHECKING:
    from pathlib import Path


def test_main_check_committed_wire_module_exits_clean() -> None:
    assert generate_wire.main(["--check"]) == 0


def test_render_core_vocabulary_names_enums_and_constants() -> None:
    rendered = generate_wire.render_core()

    assert 'export enum SIGNAL_TYPE {\n    OFFER = "offer",' in rendered
    assert "export enum ERROR_REASON {" in rendered
    assert "export enum ROOM_CLOSED_REASON {" in rendered
    assert 'export const WEBSOCKET_PATH = "/ws";' in rendered
    assert "export const SESSION_ENDED_CLOSE_CODE = 4002;" in rendered
    assert "export const REPLAY_LOG_LIMIT = 256;" in rendered
    description_types = "export const DESCRIPTION_TYPES: readonly SIGNAL_TYPE[]"
    assert f"{description_types} = [SIGNAL_TYPE.ANSWER, SIGNAL_TYPE.OFFER];" in rendered
    assert "const" + " enum" not in rendered


def written_by_main(root: Path) -> dict[Path, str]:
    return {path: path.read_text() for path in generate_wire.outputs(root)}


def test_main_without_check_writes_every_rendered_file_under_the_root(tmp_path: Path) -> None:
    assert generate_wire.main([], tmp_path) == 0
    assert written_by_main(tmp_path) == generate_wire.outputs(tmp_path)


def test_main_check_in_sync_files_exits_clean_and_writes_nothing(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)
    before = written_by_main(tmp_path)

    assert generate_wire.main(["--check"], tmp_path) == 0
    assert written_by_main(tmp_path) == before


def test_main_check_drifted_file_exits_with_message(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)
    [drifted, *_] = generate_wire.outputs(tmp_path)
    drifted.write_text(drifted.read_text() + "// hand edit\n")

    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], tmp_path)


def test_main_check_missing_file_exits_with_message_and_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_render_core_line_of_exactly_the_print_width_stays_on_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    one_line = "export const DESCRIPTION_TYPES: readonly SIGNAL_TYPE[] = [SIGNAL_TYPE.ANSWER, SIGNAL_TYPE.OFFER];"
    monkeypatch.setattr(generate_wire, "PRINT_WIDTH", len(one_line))

    assert f"\n{one_line}\n" in generate_wire.render_core()

    monkeypatch.setattr(generate_wire, "PRINT_WIDTH", len(one_line) - 1)

    assert f"\n{one_line}\n" not in generate_wire.render_core()
