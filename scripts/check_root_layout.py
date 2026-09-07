"""The root layout gate: the repository root holds only what a tool must find there.

The root carries the package manifest and its lockfile, the pre-commit and
scanner configuration, the licence, and the entry documents. Everything else
lives in the directory of its owner (AGENTS.md, "Repository layout"): a helper
under `scripts/`, a fixture next to its test. The gate reads the tree as git
sees it -- tracked files and untracked files git does not ignore -- so a file
that strays onto a runner is red too, and every refusal names where the file
belongs.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".pre-commit-config.yaml",
        "AGENTS.md",
        "CLAUDE.md",
        "LICENSE",
        "Makefile",
        "README.md",
        "pyproject.toml",
        "sonar-project.properties",
        "uv.lock",
    }
)
ROOT_DIRECTORIES = frozenset({".github", "scripts", "src", "tests"})
HOME_BY_SUFFIX = {
    ".py": "a helper belongs under scripts/, a fixture next to its test",
    ".md": "a document belongs next to its owner",
}
DEFAULT_HOME = "the root holds only what a tool must find there; a file lives in the directory of its owner"
DIRECTORY_HOME = "a new top-level directory needs a named owner and an entry in scripts/check_root_layout.py"
# The gate judges the repository that carries it, never the caller's working
# directory: `git ls-files` prints paths relative to the cwd, so a run from
# `scripts/` would otherwise accuse this very file of being misplaced.
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RootAllowlist:
    files: frozenset[str]
    directories: frozenset[str]


REPOSITORY_ALLOWLIST = RootAllowlist(ROOT_FILES, ROOT_DIRECTORIES)


def root_layout_problems(listing: Iterable[str], allowlist: RootAllowlist) -> tuple[str, ...]:
    """What the listing carries at the root that the allowlist does not name.

    `listing` holds repository-relative paths as git prints them; an entry with
    a separator lives in a top-level directory, one without is a root file.
    """
    files: set[str] = set()
    directories: set[str] = set()
    for entry in listing:
        top, separator, _ = entry.partition("/")
        (directories if separator else files).add(top)
    problems = [
        f"{name}: {HOME_BY_SUFFIX.get(Path(name).suffix, DEFAULT_HOME)}" for name in sorted(files - allowlist.files)
    ]
    problems.extend(f"{name}/: {DIRECTORY_HOME}" for name in sorted(directories - allowlist.directories))
    return tuple(problems)


def _git_listing(project_root: Path, *options: str) -> list[str]:
    completed = subprocess.run(  # noqa: S603 -- fixed, literal argv; no untrusted input
        ["git", "ls-files", "-z", *options],  # noqa: S607 -- "git" resolved via PATH by design
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in completed.stdout.split("\0") if path]


def repository_listing(project_root: Path) -> list[str]:
    """Every tracked path plus every untracked path git does not ignore."""
    return _git_listing(project_root) + _git_listing(project_root, "--others", "--exclude-standard")


def main(project_root: Path) -> int:
    problems = root_layout_problems(repository_listing(project_root), REPOSITORY_ALLOWLIST)
    if problems:
        # A CLI gate reports to the terminal, not through structlog: it runs
        # in CI before the application, and its readers are humans in a log.
        print(  # noqa: T201
            "root layout check failed:\n  " + "\n  ".join(problems), file=sys.stderr
        )
        return 1
    print("root layout check passed", flush=True)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(REPO_ROOT))
