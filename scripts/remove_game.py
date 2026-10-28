"""Remove the AI game from a throwaway copy of this checkout.

CI runs it on a copy and then proves the call still works: removing the game is
exactly this deletion, plus the compose network and the doc paragraphs. It
refuses to touch the checkout it lives in, and fails when anything it expects
to delete is missing, so the drill cannot pass by deleting nothing.
"""

import shutil
import subprocess
import sys
from pathlib import Path

GAME_PATHS = (
    "src/hopin/game",
    "src/hopin/web/static/assets/game",
    "tests/game",
    "tests/e2e/game",
)
MARKED_FILES = ("src/hopin/main.py", "tests/conftest.py")
GAME_MARKER = "# game"
GAME_ONLY_PACKAGES = ("overnightworks-agent-runtime-client", "httpx2")
# Starlette's TestClient, which the core's tests drive, needs httpx2: it stays as a dev dependency.
TEST_ONLY_PACKAGE = "httpx2"
REPOSITORY = Path(__file__).resolve().parent.parent


def remove_paths(copy: Path) -> None:
    for relative in GAME_PATHS:
        target = copy / relative
        if not target.is_dir():
            raise SystemExit(f"remove_game: {relative} is missing in {copy}")
        shutil.rmtree(target)


def remove_marked_lines(copy: Path) -> None:
    for relative in MARKED_FILES:
        path = copy / relative
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not line.rstrip().endswith(GAME_MARKER)]
        if len(kept) == len(lines):
            raise SystemExit(f"remove_game: {relative} has no line marked {GAME_MARKER!r}")
        path.write_text("".join(kept))


def remove_packages(copy: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("remove_game: uv is not on PATH")
    # The arguments are this file's constants; --no-sync leaves installing to the caller.
    subprocess.run([uv, "remove", "--no-sync", *GAME_ONLY_PACKAGES], cwd=copy, check=True)  # noqa: S603
    subprocess.run([uv, "add", "--no-sync", "--dev", TEST_ONLY_PACKAGE], cwd=copy, check=True)  # noqa: S603


def main(arguments: list[str]) -> None:
    if len(arguments) != 1:
        raise SystemExit("usage: remove_game.py <copy of the checkout>")
    copy = Path(arguments[0]).resolve()
    if copy == REPOSITORY:
        raise SystemExit("remove_game: refusing to edit the repository this script lives in; pass a copy")
    remove_paths(copy)
    remove_marked_lines(copy)
    remove_packages(copy)


if __name__ == "__main__":
    main(sys.argv[1:])
