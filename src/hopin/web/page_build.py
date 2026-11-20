"""The page build `make frontend` writes into `web/build/` (never committed), read once at start.

The server refuses to start without it. The Content-Security-Policy admits the shell's inline scripts by hash, read from
the built shell at start and never `'unsafe-inline'`, and in `style-src` exactly the hash of the one style SvelteKit's
route announcer writes, read from the build's `immutable/` JavaScript at start. A build with no such style, or with two
different ones, refuses the start. `hopin.css` hides `#svelte-announcer` itself, so the page does not depend on the
admitted style, and every other inline style stays refused. The policy and a Permissions-Policy are installed by
`app.py` through webauth's security headers.
"""

import base64
import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.responses import Response
from webauth.languages import AppLanguages

from hopin.config.secret_files import StartRefusedError
from hopin.words import LANGUAGES, SOURCE_LANGUAGE

if TYPE_CHECKING:
    from collections.abc import Mapping

BUILD_DIRECTORY = Path(__file__).parent / "build"
SHELL_FILE = "index.html"
SIGNED_OUT_FILE = "login.html"
LOGIN_FAILED_FILE = "login-failed.html"
NO_ACCESS_FILE = "no-access.html"
APP_PATH_PREFIX = "/_app/"
APP_DIRECTORY = "_app"
IMMUTABLE_DIRECTORY = "immutable"
ANNOUNCER_ID = "svelte-announcer"
HTML_LANGUAGE = re.compile(rb'(<html\b[^>]*?\blang=")[^"]*(")')
APP_LANGUAGES = AppLanguages(offered=LANGUAGES, default=SOURCE_LANGUAGE)
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


class _LinkedFiles(HTMLParser):
    """The `/_app/` `href` of every `<link rel="stylesheet">` and every `<link rel="icon">`."""

    def __init__(self) -> None:
        super().__init__()
        self.stylesheets: list[str] = []
        self.icons: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        href = values.get("href")
        if tag != "link" or not href or not href.startswith(APP_PATH_PREFIX):
            return
        if values.get("rel") == "stylesheet":
            self.stylesheets.append(href)
        elif values.get("rel") == "icon":
            self.icons.append(href)


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


def _linked_app_files(page: bytes) -> _LinkedFiles:
    linked = _LinkedFiles()
    linked.feed(page.decode("utf-8"))
    linked.close()
    return linked


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
class PageVariant:
    """One page's bytes in one language, with the strong ETag of those bytes."""

    content: bytes
    etag: str


@dataclass(frozen=True)
class BuiltPage:
    """One page as `make frontend` wrote it for each offered language, each with its own strong ETag."""

    variants: Mapping[str, PageVariant]

    @classmethod
    def of(cls, content_of: Mapping[str, bytes], name: str) -> BuiltPage:
        """The page as built for each language, its `<html lang>` set to that language, made once so no request
        rewrites it."""
        if any(HTML_LANGUAGE.search(content) is None for content in content_of.values()):
            raise PageBuildMissingError(f"The page {name} of the build has no <html lang>; run make frontend")
        return cls(variants={language: _page_variant(content, language) for language, content in content_of.items()})


def _page_variant(content: bytes, language: str) -> PageVariant:
    variant = HTML_LANGUAGE.sub(rb"\g<1>" + language.encode() + rb"\g<2>", content, count=1)
    return PageVariant(content=variant, etag=f'"{hashlib.sha256(variant).hexdigest()}"')


def built_page_response(page: BuiltPage, language: str, status_code: int = 200) -> Response:
    variant = page.variants[language]
    # The language follows the session's account tag first and `Accept-Language` second, so a cache keys on both.
    headers = {"ETag": variant.etag, "Vary": "Accept-Language, Cookie"}
    return Response(variant.content, status_code=status_code, media_type="text/html", headers=headers)


@dataclass(frozen=True)
class PageBuild:
    """What `make frontend` wrote: the pages the server answers with, and what their policy and gate admit.

    The pages are held as read, one per offered language, so the served shell is the hashed shell even when a build
    rewrites the file while the server runs. The shell is one file for every language, which loads its words in the
    browser; each sign-in page is prerendered once per language in `<language>/` and carries its text. `sign_in_assets` are the `/_app/` stylesheets and icon the three sign-in
    pages link and the `/_app/` files those stylesheets reference (the fonts), the only build files a signed-out
    visitor may fetch.
    `announcer_style_sources` are the `style-src` sources that admit the route announcer's inline style.
    """

    directory: Path
    languages: AppLanguages
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
    def load(cls, directory: Path, languages: AppLanguages = APP_LANGUAGES) -> PageBuild:
        sign_in_paths = {
            name: {language: directory / language / name for language in languages.offered}
            for name in (SIGNED_OUT_FILE, LOGIN_FAILED_FILE, NO_ACCESS_FILE)
        }
        page_paths = [directory / SHELL_FILE, *(path for paths in sign_in_paths.values() for path in paths.values())]
        if not all(path.is_file() for path in page_paths) or not (directory / APP_DIRECTORY).is_dir():
            raise PageBuildMissingError(f"The page build is missing at {directory}; run make frontend")
        shell_bytes = (directory / SHELL_FILE).read_bytes()
        sign_in_bytes = {
            name: {language: path.read_bytes() for language, path in paths.items()}
            for name, paths in sign_in_paths.items()
        }
        linked = [_linked_app_files(page) for pages in sign_in_bytes.values() for page in pages.values()]
        stylesheets = frozenset().union(*(files.stylesheets for files in linked))
        icons = frozenset().union(*(files.icons for files in linked))
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
            languages=languages,
            shell=BuiltPage.of(dict.fromkeys(languages.offered, shell_bytes), SHELL_FILE),
            signed_out=BuiltPage.of(sign_in_bytes[SIGNED_OUT_FILE], SIGNED_OUT_FILE),
            login_failed=BuiltPage.of(sign_in_bytes[LOGIN_FAILED_FILE], LOGIN_FAILED_FILE),
            no_access=BuiltPage.of(sign_in_bytes[NO_ACCESS_FILE], NO_ACCESS_FILE),
            script_hashes=tuple(_inline_hash(text) for text in scripts.texts),
            announcer_style_sources=("'unsafe-hashes'", f"'{_inline_hash(announcer_style)}'"),
            sign_in_assets=stylesheets | icons | frozenset().union(*(_css_asset_urls(css) for css in stylesheet_bytes)),
        )
