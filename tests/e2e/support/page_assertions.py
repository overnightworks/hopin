import re

from playwright.sync_api import Locator, Page, expect

from tests.e2e.support.media import MEDIA_PERMISSIONS

LEGAL_LINKS = {
    "Impressum": "https://hallucinai.de/impressum",
    "Datenschutz": "https://hallucinai.de/datenschutz#hopin",
}

ACCOUNT_AVATAR = ".account-button:visible"
ACCOUNT_MENU = "#accountMenu"

RECORD_POLICY_VIOLATIONS = """
window.policyViolations = [];
document.addEventListener("securitypolicyviolation", (event) => window.policyViolations.push(event.violatedDirective));
"""

# Each visible entry of More by its name, in order: an item by the first words of its label, a link of the legal row
# by its text.
VISIBLE_MORE_ENTRIES = """
() => [...document.querySelectorAll("#moreMenu .mi, #moreMenu nav a")]
    .filter((element) => element.getClientRects().length)
    .map((element) =>
        element.matches("a") ? element.textContent : element.querySelector(".grow").firstChild.textContent,
    )
"""
MESSAGES_IN_A_LONG_HISTORY = 60
LIST_IS_AT_THE_BOTTOM = "(list) => list.scrollHeight - list.scrollTop - list.clientHeight < 2"
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


def expect_no_legal_footer(screen: Locator) -> None:
    expect(screen.get_by_role("navigation", name="Legal")).to_have_count(0)


def expect_both_legal_links_in_the_profile_menu(person: Page) -> None:
    person.locator(ACCOUNT_AVATAR).click()
    expect_both_legal_links(person.locator(ACCOUNT_MENU))
    person.keyboard.press("Escape")


def expect_both_legal_links_in_the_same_tab(screen: Locator) -> None:
    for label, url in LEGAL_LINKS.items():
        link = screen.get_by_role("link", name=label)
        expect(link).to_be_visible()
        expect(link).to_have_attribute("href", url)
        expect(link).not_to_have_attribute("target", re.compile(r"."))


def expect_toast(host: Page, text: str, icon_path: str) -> None:
    expect(host.locator(TOASTS)).to_have_text(text)
    expect(host.locator(TOAST_ICON)).to_have_attribute("d", icon_path)
