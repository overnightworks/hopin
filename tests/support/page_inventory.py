"""A page's markup read into what the page guards judge: links, forms, stylesheets and screens."""

import re
from html.parser import HTMLParser

LEGAL_URLS = ("https://hallucinai.de/impressum", "https://hallucinai.de/datenschutz#hopin")
NEW_TAB_LINK_ATTRIBUTES = {"target": "_blank", "rel": "noopener"}
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
URL_ATTRIBUTES = {"src", "href", "xlink:href"}


class PageInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inline_scripts = 0
        self.style_elements = 0
        self.forbidden_attributes: list[str] = []
        self.fetched_urls: list[str] = []
        self.links: list[dict[str, str | None]] = []
        self.forms: list[dict[str, str | None]] = []
        self.submit_buttons = 0
        self.stylesheets: list[str] = []
        self.screens: set[str] = set()
        self.legal_navs_by_screen: dict[str, list[list[str]]] = {}
        self._screens: list[tuple[str, int]] = []
        self._legal_nav_links: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self._depth += 1
        attributes = dict(attrs)
        self._track_screen(attributes)
        self._track_legal_nav(tag, attributes)
        self._inspect(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._inspect(tag, attrs)

    def _inspect(self, tag, attrs):
        attributes = dict(attrs)
        self._inspect_head(tag, attributes)
        self._inspect_body(tag, attributes)
        self._inspect_attributes(tag, attrs)

    def _inspect_head(self, tag, attributes):
        if tag == "style":
            self.style_elements += 1
        if tag == "script" and "src" not in attributes:
            self.inline_scripts += 1
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheets.append(attributes["href"])

    def _inspect_body(self, tag, attributes):
        if tag == "form":
            self.forms.append(attributes)
        if tag == "button" and attributes.get("type") == "submit":
            self.submit_buttons += 1
        if tag == "a":
            self.links.append(attributes)
            if self._legal_nav_links is not None:
                self._legal_nav_links.append(attributes["href"])

    def _inspect_attributes(self, tag, attrs):
        for name, value in attrs:
            if name == "style" or name.startswith("on"):
                self.forbidden_attributes.append(name)
            if name in URL_ATTRIBUTES and tag != "a":
                self.fetched_urls.append(value)
            if value is not None and value.strip().lower().startswith("javascript:"):
                self.forbidden_attributes.append(f"{name}=javascript:")

    def handle_endtag(self, tag):
        if tag == "nav" and self._legal_nav_links is not None:
            self._record_legal_nav()
        while self._screens and self._screens[-1][1] == self._depth:
            self._screens.pop()
        self._depth -= 1

    def _track_screen(self, attributes):
        if "data-screen" in attributes:
            self.screens.add(attributes["data-screen"])
            self._screens.append((attributes["data-screen"], self._depth))

    def _track_legal_nav(self, tag, attributes):
        if tag == "nav" and "legal" in (attributes.get("class") or "").split():
            self._legal_nav_links = []

    def _record_legal_nav(self):
        screen = self._screens[-1][0] if self._screens else ""
        self.legal_navs_by_screen.setdefault(screen, []).append(self._legal_nav_links)
        self._legal_nav_links = None


def inventory_of(markup: str) -> PageInventory:
    inventory = PageInventory()
    inventory.feed(markup)
    return inventory
