import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import check_root_layout

ALLOWLIST = check_root_layout.REPOSITORY_ALLOWLIST
GATE = Path(check_root_layout.__file__).resolve()
# The identity travels on the command line, so the probe repository commits on a
# machine and on a runner that configure none.
GIT_IDENTITY = ("-c", "user.name=layout gate test", "-c", "user.email=gate@example.invalid")


def git(repository: Path, *arguments: str) -> None:
    # The argv is built from this test's own literals (S603), and "git" is
    # left to PATH by design (S607). A `noqa` directive carries codes only.
    subprocess.run(  # noqa: S603
        ["git", *GIT_IDENTITY, *arguments],  # noqa: S607
        cwd=repository,
        check=True,
        capture_output=True,
    )


def commit_the_tree(repository: Path, message: str) -> None:
    """Make the probe tree tracked, as a checked-out repository is."""
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "--message", message)


@pytest.fixture
def gate_repository(tmp_path, monkeypatch):
    """A git repository carrying the gate under scripts/, as this repository does.

    Its tree is committed, because tracked and untracked paths reach the gate
    through separate git calls and a fixture that only writes leaves the tracked
    half unasserted.

    The git configuration is pinned to the null device for this test and every
    process it starts: the gate asks git which untracked files are ignored, so a
    machine-wide ignore matching a probe file would otherwise turn a red path
    green on that machine only.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    (tmp_path / "scripts").mkdir()
    shutil.copy(GATE, tmp_path / "scripts" / GATE.name)
    (tmp_path / "README.md").write_text("a repository the gate is happy with\n")
    git(tmp_path, "init", "--quiet")
    commit_the_tree(tmp_path, "the probe tree")
    return tmp_path


def run_gate(repository: Path, working_directory: Path) -> subprocess.CompletedProcess[str]:
    """The gate as CI runs it: a python process over the script, started somewhere."""
    # The argv is built from this test's own paths, so no untrusted input
    # reaches the call (S603). A `noqa` directive carries codes only.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(repository / "scripts" / GATE.name)],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_gate_exits_zero_when_it_passes(gate_repository):
    """The exit status CI reads, which a return value in process does not prove."""
    completed = run_gate(gate_repository, gate_repository)

    assert completed.returncode == 0
    assert "passed" in completed.stdout


@pytest.mark.parametrize("working_directory", [".", "scripts"])
def test_the_gate_names_a_stray_root_file_and_fails(gate_repository, working_directory):
    (gate_repository / "NOTES.md").write_text("a stray at the root\n")

    completed = run_gate(gate_repository, gate_repository / working_directory)

    assert completed.returncode == 1
    assert "NOTES.md" in completed.stderr


def test_the_gate_names_a_committed_stray_root_file_and_fails(gate_repository):
    """A stray that already landed is as red as one that was never tracked."""
    (gate_repository / "NOTES.md").write_text("a stray somebody committed\n")
    commit_the_tree(gate_repository, "a stray at the root")

    completed = run_gate(gate_repository, gate_repository)

    assert completed.returncode == 1
    assert "NOTES.md" in completed.stderr


# The cases below call the gate in process instead of through `run_gate`.
# `run_gate` starts a python process over a *copy* of the gate, so nothing it
# exercises is ever recorded against the file this repository ships: driven
# only that way, the gate's git boundary and its reporting read as untested,
# and the scanner sees an analysed file with no coverage. They repeat what the
# process cases above already prove for that reason alone, and the process
# cases stay because only they answer what CI reads -- the exit status either
# way, and that the gate judges the repository carrying it whatever directory
# it was started from.


def test_the_listing_carries_the_tracked_and_the_untracked_half(gate_repository):
    """Both halves reach the gate: a file that landed and one that only strayed onto the tree."""
    (gate_repository / "untracked.md").write_text("a file nobody committed\n")

    listing = check_root_layout.repository_listing(gate_repository)

    assert "README.md" in listing
    assert "untracked.md" in listing


def test_the_gate_reports_success_on_an_allowlisted_tree(gate_repository, capsys):
    assert check_root_layout.main(gate_repository) == 0
    assert "passed" in capsys.readouterr().out


def test_the_gate_names_every_problem_it_found(gate_repository, capsys):
    """A failing run reports all of it, not the first thing it tripped over."""
    (gate_repository / "stray.txt").write_text("a stray at the root\n")
    (gate_repository / "tooling").mkdir()
    (gate_repository / "tooling" / "helper.py").write_text("a stray directory\n")

    assert check_root_layout.main(gate_repository) == 1

    reported = capsys.readouterr().err
    assert f"stray.txt: {check_root_layout.DEFAULT_HOME}" in reported
    assert f"tooling/: {check_root_layout.DIRECTORY_HOME}" in reported


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
