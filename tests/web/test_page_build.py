from typing import TYPE_CHECKING

import pytest

from hopin.config.secret_files import StartRefusedError
from hopin.web.page_build import PageBuild
from tests.support.page_build import FIXTURE_DIRECTORY, FIXTURE_SCRIPT_HASH

if TYPE_CHECKING:
    from pathlib import Path


def build_with_shell(root: Path, shell: str) -> Path:
    (root / "_app").mkdir(parents=True)
    (root / "index.html").write_text(shell, encoding="utf-8")
    return root


class TestLoad:
    def test_the_committed_fixture_loads_with_the_known_hash_of_its_one_inline_script(self):
        page_build = PageBuild.load(FIXTURE_DIRECTORY)

        assert page_build.directory == FIXTURE_DIRECTORY
        assert page_build.script_hashes == (FIXTURE_SCRIPT_HASH,)

    @pytest.mark.parametrize(
        ("shell", "hashes"),
        [
            ("<html><body>no script</body></html>", ()),
            ('<script src="/a.js"></script>', ()),
            ("<script>first();</script>", ("sha256-zLkkCRwXnY9Po9RpPpnc0FGgkj110bMQ+s91j2btock=",)),
            (
                '<script>first();</script><script src="/a.js"></script><script>second();</script>',
                (
                    "sha256-zLkkCRwXnY9Po9RpPpnc0FGgkj110bMQ+s91j2btock=",
                    "sha256-dGCVYAQKA3St4oajZNnYqanOSQTbzydEw1qtGC2p8B4=",
                ),
            ),
            (
                "<script>if (a < b && c) { go(); }</script>",
                ("sha256-AN1wiKAb/Pu7jSZZAf3Ia9nFQlkK5Nhh7LQo7YqHHIY=",),
            ),
        ],
        ids=["none", "src-only", "one", "several", "text-the-parser-hands-over-in-chunks"],
    )
    def test_the_hashes_are_those_of_every_inline_script_in_order(self, tmp_path, shell, hashes):
        assert PageBuild.load(build_with_shell(tmp_path, shell)).script_hashes == hashes

    @pytest.mark.parametrize("shell", ["<html></html>", "<html>other</html>"])
    def test_the_shell_is_the_bytes_read_and_its_strong_etag_follows_them(self, tmp_path, shell):
        page_build = PageBuild.load(build_with_shell(tmp_path, shell))

        assert page_build.shell == shell.encode()
        assert page_build.shell_etag.startswith('"')
        assert page_build.shell_etag.endswith('"')

    def test_the_etag_of_known_bytes_is_the_quoted_sha256_of_them(self, tmp_path):
        page_build = PageBuild.load(build_with_shell(tmp_path, "<html></html>"))

        assert page_build.shell_etag == '"b633a587c652d02386c4f16f8c6f6aab7352d97f16367c3c40576214372dd628"'

    def test_a_shell_written_after_the_load_changes_neither_the_bytes_nor_the_hashes(self, tmp_path):
        root = build_with_shell(tmp_path, "<script>first();</script>")
        page_build = PageBuild.load(root)

        (root / "index.html").write_text("<script>second();</script>", encoding="utf-8")

        assert page_build.shell == b"<script>first();</script>"
        assert page_build.script_hashes == ("sha256-zLkkCRwXnY9Po9RpPpnc0FGgkj110bMQ+s91j2btock=",)

    def test_a_missing_directory_refuses_the_start_and_names_the_build_command(self, tmp_path):
        with pytest.raises(StartRefusedError, match="make frontend"):
            PageBuild.load(tmp_path / "absent")

    def test_a_directory_without_the_shell_refuses_the_start_and_names_the_build_command(self, tmp_path):
        (tmp_path / "_app").mkdir()

        with pytest.raises(StartRefusedError, match="make frontend"):
            PageBuild.load(tmp_path)

    def test_a_directory_without_the_app_files_refuses_the_start_and_names_the_build_command(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

        with pytest.raises(StartRefusedError, match="make frontend"):
            PageBuild.load(tmp_path)
