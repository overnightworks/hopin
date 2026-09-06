import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import check_root_layout

ALLOWLIST = check_root_layout.REPOSITORY_ALLOWLIST
GATE = Path(check_root_layout.__file__).resolve()


@pytest.fixture
def gate_repository(tmp_path):
    """A git repository carrying the gate under scripts/, as this repository does."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(GATE, tmp_path / "scripts" / GATE.name)
    (tmp_path / "README.md").write_text("a repository the gate is happy with\n")
    subprocess.run(
        ["git", "init", "--quiet"],  # noqa: S607 -- "git" resolved via PATH by design
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def run_gate(repository: Path, working_directory: Path) -> subprocess.CompletedProcess[str]:
    """The gate as CI runs it: a python process over the script, started somewhere."""
    return subprocess.run(  # noqa: S603 -- fixed argv built from the test's own paths
        [sys.executable, str(repository / "scripts" / GATE.name)],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_gate_passes_on_an_allowlisted_tree(gate_repository):
    completed = run_gate(gate_repository, gate_repository)

    assert completed.returncode == 0
    assert "passed" in completed.stdout


@pytest.mark.parametrize("working_directory", [".", "scripts"])
def test_the_gate_names_a_stray_root_file_and_fails(gate_repository, working_directory):
    (gate_repository / "NOTES.md").write_text("a stray at the root\n")

    completed = run_gate(gate_repository, gate_repository / working_directory)

    assert completed.returncode == 1
    assert "NOTES.md" in completed.stderr


def test_this_repository_passes_its_own_gate():
    listing = check_root_layout.repository_listing(check_root_layout.REPO_ROOT)

    assert check_root_layout.root_layout_problems(listing, ALLOWLIST) == ()


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
        "tests/scripts/test_check_root_layout.py",
    ]

    assert check_root_layout.root_layout_problems(listing, ALLOWLIST) == ()


@pytest.mark.parametrize(
    ("stray", "home"),
    [
        ("NOTES.md", check_root_layout.HOME_BY_SUFFIX[".md"]),
        ("helper.py", check_root_layout.HOME_BY_SUFFIX[".py"]),
        ("stray.txt", check_root_layout.DEFAULT_HOME),
    ],
)
def test_stray_root_file_fails_with_the_sentence_where_it_belongs(stray, home):
    problems = check_root_layout.root_layout_problems(["README.md", stray], ALLOWLIST)

    assert problems == (f"{stray}: {home}",)


def test_stray_root_directory_fails_with_the_sentence_where_it_belongs():
    problems = check_root_layout.root_layout_problems(["README.md", "tooling/helper.py"], ALLOWLIST)

    assert problems == (f"tooling/: {check_root_layout.DIRECTORY_HOME}",)
