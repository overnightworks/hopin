from typing import TYPE_CHECKING

import pytest

from hopin.game.vocabulary import BROWSER_ENUMS, FEATURE_NAME
from scripts import generate_wire

if TYPE_CHECKING:
    from pathlib import Path


def game_output(root: Path) -> Path:
    [output] = [path for path in generate_wire.outputs(root) if path.parent.name == FEATURE_NAME]
    return output


def test_the_generator_writes_the_games_enums_beside_the_game_from_their_owners(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)

    written = game_output(tmp_path).read_text()
    for exported_name, vocabulary in BROWSER_ENUMS.items():
        assert f"export enum {exported_name} {{" in written
        for name, member in vocabulary.__members__.items():
            assert f'    {name} = "{member.value}",' in written


def test_the_check_fails_when_the_games_file_drifts(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)
    output = game_output(tmp_path)
    output.write_text(output.read_text() + "// hand edit\n")

    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], tmp_path)
