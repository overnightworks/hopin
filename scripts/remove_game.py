"""Remove the AI game from a throwaway copy of this checkout.

CI runs it on a copy and then proves the call still works: removing the game is
exactly this deletion, plus the compose network, the four `HOPIN_AGENT__` and
the `HOPIN_SPEECH__` variables, the `litellm_key` mount and declaration, and
the doc paragraphs. Participation and its MCP connector (`agent/mcp.py`,
`agent/tools.py`) stay, and with them the agent runtime's client without its
`backend` extra. It refuses to touch the checkout it lives in, and fails
when anything it expects to delete is missing, so the drill cannot pass by
deleting nothing.
"""

import shutil
import subprocess
import sys
from pathlib import Path

GAME_DIRECTORIES = (
    "src/hopin/game",
    "src/hopin/agents",
    "frontend/src/lib/features/game",
    "tests/game",
    "tests/e2e/game",
)
GAME_FILES = (
    "src/hopin/agent/backend.py",
    "src/hopin/agent/feature.py",
    "src/hopin/agent/settings.py",
    "src/hopin/agent/speech.py",
)
MARKED_FILES = ("src/hopin/main.py", "tests/conftest.py", "scripts/generate_wire.py", "pyproject.toml")
GAME_MARKER = "# game"
GAME_ONLY_PACKAGES = ("overnightworks-speech-client",)
# The client stays for the MCP connector; only the extra that carries its HTTP library to the gateway goes.
CLIENT_EXTRAS = ("[backend,mcp-server]", "[mcp-server]")
REPOSITORY = Path(__file__).resolve().parent.parent


def remove_paths(copy: Path) -> None:
    for relative in GAME_DIRECTORIES:
        target = copy / relative
        if not target.is_dir():
            raise SystemExit(f"remove_game: {relative} is missing in {copy}")
        shutil.rmtree(target)
    for relative in GAME_FILES:
        target = copy / relative
        if not target.is_file():
            raise SystemExit(f"remove_game: {relative} is missing in {copy}")
        target.unlink()


def remove_marked_lines(copy: Path) -> None:
    for relative in MARKED_FILES:
        path = copy / relative
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not line.rstrip().endswith(GAME_MARKER)]
        if len(kept) == len(lines):
            raise SystemExit(f"remove_game: {relative} has no line marked {GAME_MARKER!r}")
        path.write_text("".join(kept))


def drop_client_extra(copy: Path) -> None:
    path = copy / "pyproject.toml"
    text = path.read_text()
    before, after = CLIENT_EXTRAS
    if before not in text:
        raise SystemExit(f"remove_game: pyproject.toml has no dependency on {before}")
    path.write_text(text.replace(before, after))


def remove_packages(copy: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("remove_game: uv is not on PATH")
    # The arguments are this file's constants; --no-sync leaves installing to the caller, and the remove locks
    # the copy with the dropped extra too.
    subprocess.run([uv, "remove", "--no-sync", *GAME_ONLY_PACKAGES], cwd=copy, check=True)  # noqa: S603 - argv is the uv binary plus constants of this file


def main(arguments: list[str]) -> None:
    if len(arguments) != 1:
        raise SystemExit("usage: remove_game.py <copy of the checkout>")
    copy = Path(arguments[0]).resolve()
    if copy == REPOSITORY:
        raise SystemExit("remove_game: refusing to edit the repository this script lives in; pass a copy")
    remove_paths(copy)
    remove_marked_lines(copy)
    drop_client_extra(copy)
    remove_packages(copy)


if __name__ == "__main__":
    main(sys.argv[1:])
