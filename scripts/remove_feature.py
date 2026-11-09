"""Remove one optional feature from a throwaway copy of this checkout.

CI runs it on a copy and then proves the call still works. Removing the AI game is
this deletion, plus the compose network, the four `HOPIN_AGENT__` and the
`HOPIN_SPEECH__` variables, the `litellm_key` mount and declaration, and the doc
paragraphs. Participation and its MCP connector (`agent/mcp.py`, `agent/tools.py`)
stay, and with them the agent runtime's client without its `backend` extra.
Removing Skat is its server package, its browser module, its tests and the lines
marked `# skat`. It refuses to touch the checkout it lives in, and fails when
anything it expects to delete is missing, so the drill cannot pass by deleting
nothing.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Removal:
    """What deleting one feature takes out of a copy: its paths, and its lines in files that stay, which end in the
    marker."""

    directories: tuple[str, ...]
    files: tuple[str, ...]
    marker: str
    marked_files: tuple[str, ...]


GAME = Removal(
    directories=(
        "src/hopin/game",
        "src/hopin/agents",
        "frontend/src/lib/features/game",
        "tests/game",
        "tests/e2e/game",
    ),
    files=(
        "src/hopin/agent/backend.py",
        "src/hopin/agent/feature.py",
        "src/hopin/agent/settings.py",
        "src/hopin/agent/speech.py",
    ),
    marker="# game",
    marked_files=(
        "src/hopin/main.py",
        "tests/conftest.py",
        "scripts/generate_wire.py",
        "pyproject.toml",
        "tests/e2e/support/drive_hopin.py",
    ),
)
SKAT = Removal(
    directories=("src/hopin/skat", "frontend/src/lib/features/skat", "tests/skat", "tests/e2e/skat"),
    files=("tests/e2e/support/drive_hopin.py", "scripts/skat_joint_stack.sh"),
    marker="# skat",
    marked_files=("src/hopin/main.py", "scripts/generate_wire.py"),
)
REMOVALS = {"game": GAME, "skat": SKAT}
GAME_ONLY_PACKAGES = ("overnightworks-speech-runtime-client",)
# The client stays for the MCP connector; only the extra that carries its HTTP library to the gateway goes.
CLIENT_EXTRAS = ("[backend,mcp-server]", "[mcp-server]")
REPOSITORY = Path(__file__).resolve().parent.parent


def remove_paths(removal: Removal, copy: Path) -> None:
    for relative in removal.directories:
        target = copy / relative
        if not target.is_dir():
            raise SystemExit(f"remove_feature: {relative} is missing in {copy}")
        shutil.rmtree(target)
    for relative in removal.files:
        target = copy / relative
        if not target.is_file():
            raise SystemExit(f"remove_feature: {relative} is missing in {copy}")
        target.unlink()


def remove_marked_lines(removal: Removal, copy: Path) -> None:
    for relative in removal.marked_files:
        path = copy / relative
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not line.rstrip().endswith(removal.marker)]
        if len(kept) == len(lines):
            raise SystemExit(f"remove_feature: {relative} has no line marked {removal.marker!r}")
        path.write_text("".join(kept))


def drop_client_extra(copy: Path) -> None:
    path = copy / "pyproject.toml"
    text = path.read_text()
    before, after = CLIENT_EXTRAS
    if before not in text:
        raise SystemExit(f"remove_feature: pyproject.toml has no dependency on {before}")
    path.write_text(text.replace(before, after))


def remove_packages(copy: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("remove_feature: uv is not on PATH")
    # The arguments are this file's constants; --no-sync leaves installing to the caller, and the remove locks
    # the copy with the dropped extra too.
    subprocess.run([uv, "remove", "--no-sync", *GAME_ONLY_PACKAGES], cwd=copy, check=True)  # noqa: S603 - argv is the uv binary plus constants of this file


def main(arguments: list[str]) -> None:
    if len(arguments) != 2 or arguments[0] not in REMOVALS:
        raise SystemExit(f"usage: remove_feature.py {'|'.join(REMOVALS)} <copy of the checkout>")
    feature, copy = arguments[0], Path(arguments[1]).resolve()
    if copy == REPOSITORY:
        raise SystemExit("remove_feature: refusing to edit the repository this script lives in; pass a copy")
    removal = REMOVALS[feature]
    remove_paths(removal, copy)
    remove_marked_lines(removal, copy)
    if feature == "game":
        drop_client_extra(copy)
        remove_packages(copy)


if __name__ == "__main__":
    main(sys.argv[1:])
