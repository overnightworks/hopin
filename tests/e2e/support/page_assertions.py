from playwright.sync_api import Locator, Page, expect

from tests.e2e.support.media import MEDIA_PERMISSIONS

LEGAL_LINKS = {
    "Impressum": "https://hallucinai.de/impressum",
    "Datenschutz": "https://hallucinai.de/datenschutz#hopin",
}

# Each visible entry of More by its name, in order: an item by the first words of its label, a rule as "separator".
VISIBLE_MORE_ENTRIES = """
() => [...document.querySelectorAll("#moreMenu .mi, #moreMenu .sep")]
    .filter((element) => element.getClientRects().length)
    .map((element) =>
        element.classList.contains("sep") ? "separator" : element.querySelector(".grow").firstChild.textContent,
    )
"""
TOASTS = "#toasts"
TOAST_ICON = f"{TOASTS} svg path"
CHECK_ICON_PATH = "M5 12.5l4.5 4.5L19 7.5"
X_ICON_PATH = "M6 6l12 12M18 6L6 18"
CLIPBOARD_ALLOWED = {"permissions": [*MEDIA_PERMISSIONS, "clipboard-read", "clipboard-write"]}
READ_CLIPBOARD = "() => navigator.clipboard.readText()"
STUB_SHARE = """
window.sharedArguments = [];
navigator.share = (argument) => {
    window.sharedArguments.push(argument);
    return Promise.resolve();
};
"""
REMOVE_SHARE = "delete Navigator.prototype.share;"
REJECT_SHARE_WITH = """
window.shareAttempts = 0;
navigator.share = () => {
    window.shareAttempts += 1;
    return Promise.reject(new DOMException("refused", "%s"));
};
"""
BOX_OF_SETTLED = """
async (selector) => {
    const element = document.querySelector(selector);
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
    const { left, top, right, bottom, width } = element.getBoundingClientRect();
    return { left, top, right, bottom, width };
}
"""
TOAST_AND_MENU_BOXES = """
async (menuSelector) => {
    const settledBox = async (selector) => {
        const element = document.querySelector(selector);
        await Promise.all(element.getAnimations().map((animation) => animation.finished));
        const { top, bottom } = element.getBoundingClientRect();
        return { top, bottom };
    };
    return {
        menu: menuSelector && (await settledBox(menuSelector)),
        toast: await settledBox("#toasts .toast"),
        viewport: { height: innerHeight },
    };
}
"""


def expect_both_legal_links(screen: Locator) -> None:
    for label, url in LEGAL_LINKS.items():
        link = screen.get_by_role("link", name=label)
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", url)
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener")


def expect_toast(host: Page, text: str, icon_path: str) -> None:
    expect(host.locator(TOASTS)).to_have_text(text)
    expect(host.locator(TOAST_ICON)).to_have_attribute("d", icon_path)
