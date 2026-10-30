import base64
import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from hopin.config.secret_files import StartRefusedError

BUILD_DIRECTORY = Path(__file__).parent / "build"
SHELL_FILE = "index.html"
SIGNED_OUT_FILE = "login.html"
LOGIN_FAILED_FILE = "login-failed.html"
APP_PATH_PREFIX = "/_app/"
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


class _Stylesheets(HTMLParser):
    """The `href` of every `<link rel="stylesheet">`."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.hrefs.append(str(values["href"]))


def _app_stylesheets(page: bytes) -> frozenset[str]:
    stylesheets = _Stylesheets()
    stylesheets.feed(page.decode("utf-8"))
    stylesheets.close()
    return frozenset(href for href in stylesheets.hrefs if href.startswith(APP_PATH_PREFIX))


def _script_hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


@dataclass(frozen=True)
class BuiltPage:
    """One page as `make frontend` wrote it, with the strong ETag of those bytes."""

    content: bytes
    etag: str

    @classmethod
    def of(cls, content: bytes) -> BuiltPage:
        return cls(content=content, etag=f'"{hashlib.sha256(content).hexdigest()}"')


@dataclass(frozen=True)
class PageBuild:
    """What `make frontend` wrote: the pages the server answers with, and what their policy and gate admit.

    The pages are held as read, so the served shell is the hashed shell even when a build rewrites the file
    while the server runs. `sign_in_stylesheets` are the `/_app/` stylesheets the two sign-in pages link,
    the only build files a signed-out visitor may fetch.
    """

    directory: Path
    shell: BuiltPage
    signed_out: BuiltPage
    login_failed: BuiltPage
    script_hashes: tuple[str, ...]
    sign_in_stylesheets: frozenset[str]

    @property
    def app_directory(self) -> Path:
        return self.directory / APP_DIRECTORY

    @classmethod
    def load(cls, directory: Path) -> PageBuild:
        page_paths = [directory / name for name in (SHELL_FILE, SIGNED_OUT_FILE, LOGIN_FAILED_FILE)]
        if not all(path.is_file() for path in page_paths) or not (directory / APP_DIRECTORY).is_dir():
            raise PageBuildMissingError(f"The page build is missing at {directory}; run make frontend")
        shell_bytes, signed_out_bytes, login_failed_bytes = (path.read_bytes() for path in page_paths)
        stylesheets = _app_stylesheets(signed_out_bytes) | _app_stylesheets(login_failed_bytes)
        for stylesheet in stylesheets:
            if not (directory / stylesheet.removeprefix("/")).is_file():
                raise PageBuildMissingError(f"The page build at {directory} lacks {stylesheet}; run make frontend")
        scripts = _InlineScripts()
        scripts.feed(shell_bytes.decode("utf-8"))
        scripts.close()
        return cls(
            directory=directory,
            shell=BuiltPage.of(shell_bytes),
            signed_out=BuiltPage.of(signed_out_bytes),
            login_failed=BuiltPage.of(login_failed_bytes),
            script_hashes=tuple(_script_hash(text) for text in scripts.texts),
            sign_in_stylesheets=stylesheets,
        )
