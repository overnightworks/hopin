import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_root_layout  # noqa: E402

ALLOWLIST = check_root_layout.REPOSITORY_ALLOWLIST


def test_allowed_root_tree_passes():
    listing = [
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
        ".github/workflows/ci.yml",
        "scripts/check_root_layout.py",
        "src/hopin/main.py",
        "tests/tooling/test_check_root_layout.py",
    ]

    assert check_root_layout.root_layout_problems(listing, ALLOWLIST) == ()


def test_stray_root_file_fails_with_the_sentence_where_it_belongs():
    listing = ["README.md", "NOTES.md"]

    problems = check_root_layout.root_layout_problems(listing, ALLOWLIST)

    assert problems == (f"NOTES.md: {check_root_layout.HOME_BY_SUFFIX['.md']}",)


def test_stray_root_directory_fails_with_the_sentence_where_it_belongs():
    listing = ["README.md", "tooling/helper.py"]

    problems = check_root_layout.root_layout_problems(listing, ALLOWLIST)

    assert problems == (f"tooling/: {check_root_layout.DIRECTORY_HOME}",)
