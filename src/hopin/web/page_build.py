import base64
import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from fastapi.responses import Response

from hopin.config.secret_files import StartRefusedError

BUILD_DIRECTORY = Path(__file__).parent / "build"
SHELL_FILE = "index.html"
SIGNED_OUT_FILE = "login.html"
LOGIN_FAILED_FILE = "login-failed.html"
NO_ACCESS_FILE = "no-access.html"
APP_PATH_PREFIX = "/_app/"
APP_DIRECTORY = "_app"
IMMUTABLE_DIRECTORY = "immutable"
ANNOUNCER_ID = "svelte-announcer"
_CSS_URL = re.compile(r"""url\(\s*['"]?(/_app/[^'")\s]+)""")


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


class _AnnouncerStyles(HTMLParser):
    """The `style` of every element whose `id` is the route announcer's."""

    def __init__(self) -> None:
        super().__init__()
        self.styles: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        style = values.get("style")
        if values.get("id") == ANNOUNCER_ID and style is not None:
            self.styles.append(style)


def _app_stylesheets(page: bytes) -> frozenset[str]:
    stylesheets = _Stylesheets()
    stylesheets.feed(page.decode("utf-8"))
    stylesheets.close()
    return frozenset(href for href in stylesheets.hrefs if href.startswith(APP_PATH_PREFIX))


def _css_asset_urls(css: bytes) -> frozenset[str]:
    return frozenset(_CSS_URL.findall(css.decode("utf-8")))


def _inline_hash(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def _announcer_style(directory: Path) -> str:
    """The one style the build's immutable JavaScript writes on the route announcer's template.

    SvelteKit writes that style only in the browser, so the bundle is the one place it can be read. The template
    sits in a JavaScript string, and only chunks that name the announcer are parsed for it.
    """
    styles: set[str] = set()
    for chunk in sorted((directory / APP_DIRECTORY / IMMUTABLE_DIRECTORY).rglob("*.js")):
        text = chunk.read_bytes().decode("utf-8")
        if ANNOUNCER_ID not in text:
            continue
        parser = _AnnouncerStyles()
        parser.feed(text)
        parser.close()
        styles.update(parser.styles)
    if len(styles) != 1:
        raise PageBuildMissingError(
            f"The page build at {directory} must hold one route announcer style in its immutable files, "
            f"found {len(styles)}; run make frontend"
        )
    return next(iter(styles))


@dataclass(frozen=True)
class BuiltPage:
    """One page as `make frontend` wrote it, with the strong ETag of those bytes."""

    content: bytes
    etag: str

    @classmethod
    def of(cls, content: bytes) -> BuiltPage:
        return cls(content=content, etag=f'"{hashlib.sha256(content).hexdigest()}"')


def built_page_response(page: BuiltPage, status_code: int = 200) -> Response:
    return Response(page.content, status_code=status_code, media_type="text/html", headers={"ETag": page.etag})


@dataclass(frozen=True)
class PageBuild:
    """What `make frontend` wrote: the pages the server answers with, and what their policy and gate admit.

    The pages are held as read, so the served shell is the hashed shell even when a build rewrites the file
    while the server runs. `sign_in_assets` are the `/_app/` stylesheets the three sign-in pages link and the
    `/_app/` files those stylesheets reference (the fonts), the only build files a signed-out visitor may fetch.
    `announcer_style_sources` are the `style-src` sources that admit the route announcer's inline style.
    """

    directory: Path
    shell: BuiltPage
    signed_out: BuiltPage
    login_failed: BuiltPage
    no_access: BuiltPage
    script_hashes: tuple[str, ...]
    announcer_style_sources: tuple[str, str]
    sign_in_assets: frozenset[str]

    @property
    def app_directory(self) -> Path:
        return self.directory / APP_DIRECTORY

    @classmethod
    def load(cls, directory: Path) -> PageBuild:
        page_paths = [directory / name for name in (SHELL_FILE, SIGNED_OUT_FILE, LOGIN_FAILED_FILE, NO_ACCESS_FILE)]
        if not all(path.is_file() for path in page_paths) or not (directory / APP_DIRECTORY).is_dir():
            raise PageBuildMissingError(f"The page build is missing at {directory}; run make frontend")
        shell_bytes, signed_out_bytes, login_failed_bytes, no_access_bytes = (path.read_bytes() for path in page_paths)
        stylesheets = frozenset().union(
            *(_app_stylesheets(page) for page in (signed_out_bytes, login_failed_bytes, no_access_bytes))
        )
        stylesheet_bytes = []
        for stylesheet in stylesheets:
            if not (directory / stylesheet.removeprefix("/")).is_file():
                raise PageBuildMissingError(f"The page build at {directory} lacks {stylesheet}; run make frontend")
            stylesheet_bytes.append((directory / stylesheet.removeprefix("/")).read_bytes())
        scripts = _InlineScripts()
        scripts.feed(shell_bytes.decode("utf-8"))
        scripts.close()
        announcer_style = _announcer_style(directory)
        return cls(
            directory=directory,
            shell=BuiltPage.of(shell_bytes),
            signed_out=BuiltPage.of(signed_out_bytes),
            login_failed=BuiltPage.of(login_failed_bytes),
            no_access=BuiltPage.of(no_access_bytes),
            script_hashes=tuple(_inline_hash(text) for text in scripts.texts),
            announcer_style_sources=("'unsafe-hashes'", f"'{_inline_hash(announcer_style)}'"),
            sign_in_assets=stylesheets.union(*(_css_asset_urls(css) for css in stylesheet_bytes)),
        )
