import base64
import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from hopin.config.secret_files import StartRefusedError

BUILD_DIRECTORY = Path(__file__).parent / "build"
SHELL_FILE = "index.html"
APP_DIRECTORY = "_app"


class PageBuildMissingError(StartRefusedError):
    """The page build is not where the server expects it."""


class _InlineScripts(HTMLParser):
    """The text of every `<script>` without `src`, each joined from the chunks the parser hands over."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []
        self._chunks: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and all(name != "src" for name, _ in attrs):
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._chunks is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._chunks is not None:
            self.texts.append("".join(self._chunks))
            self._chunks = None


def _script_hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


@dataclass(frozen=True)
class PageBuild:
    """What `make frontend` wrote: the shell the server answers with, and the hashes its policy admits.

    The shell is held as read, so the served shell is the hashed shell even when a build rewrites the file
    while the server runs.
    """

    directory: Path
    shell: bytes
    shell_etag: str
    script_hashes: tuple[str, ...]

    @property
    def app_directory(self) -> Path:
        return self.directory / APP_DIRECTORY

    @classmethod
    def load(cls, directory: Path) -> PageBuild:
        shell_path = directory / SHELL_FILE
        if not shell_path.is_file() or not (directory / APP_DIRECTORY).is_dir():
            raise PageBuildMissingError(f"The page build is missing at {directory}; run make frontend")
        shell = shell_path.read_bytes()
        scripts = _InlineScripts()
        scripts.feed(shell.decode("utf-8"))
        scripts.close()
        return cls(
            directory=directory,
            shell=shell,
            shell_etag=f'"{hashlib.sha256(shell).hexdigest()}"',
            script_hashes=tuple(_script_hash(text) for text in scripts.texts),
        )
