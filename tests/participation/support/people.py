from hopin.participation.participants import Person

ISSUER = "https://issuer.test.invalid/realms/test"


def person_named(name: str) -> Person:
    """The person behind the account `account_named(name)` seats under."""
    return Person(ISSUER, name)


def room_of(browser) -> str:
    return browser.room_id
