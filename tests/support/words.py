# Generated from the catalogs by design-catalogs. Do not edit.
# fmt: off
from string import Formatter
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

type Key = Literal[
    'call.unexpectedError',
    'end.accountButton',
    'end.retryButton',
    'end.homeButton',
    'end.rejoinButton',
    'home.accountButton',
    'home.greetingUnnamed',
    'home.greetingNamed',
    'home.invitation',
    'home.startButton',
    'prejoin.accountButton',
    'prejoin.backButton',
    'prejoin.muteButton',
    'prejoin.cameraOffButton',
]
type Language = Literal[
    'de',
]
type Category = Literal[
    'zero',
    'one',
    'two',
    'few',
    'many',
    'other',
]

SOURCE_LANGUAGE: Final[Language] = 'de'
LANGUAGES: Final[tuple[Language, ...]] = (
    'de',
)
WORDS: Final[Mapping[Language, Mapping[Key, str | Mapping[Category, str]]]] = {
    'de': {
        'call.unexpectedError': 'Etwas ist schiefgegangen. Bitte versuch es noch einmal.',
        'end.accountButton': 'Konto',
        'end.retryButton': 'Erneut versuchen',
        'end.homeButton': 'Zurück zum Start',
        'end.rejoinButton': 'Wieder beitreten',
        'home.accountButton': 'Konto',
        'home.greetingUnnamed': 'Hallo',
        'home.greetingNamed': 'Hallo, {name}',
        'home.invitation': 'Starte einen Anruf und schick den Link dann an die Leute, die du dabeihaben möchtest.',
        'home.startButton': 'Anruf starten',
        'prejoin.accountButton': 'Konto',
        'prejoin.backButton': 'Zurück',
        'prejoin.muteButton': 'Stummschalten',
        'prejoin.cameraOffButton': 'Kamera ausschalten',
    },
}


def plural_category(language: Language, count: int) -> Category:
    n = abs(count)
    match language:
        case 'de':
            if (n == 1):
                return 'one'
            return 'other'


def text(language: Language, key: Key, /, *, count: int | None = None, **gaps: str) -> str:
    words = WORDS[language][key]
    if isinstance(words, str):
        sentence, counted = words, False
    elif count is None:
        raise TypeError(f'{key!r} is a plural family: pass count')
    else:
        sentence, counted = words.get(plural_category(language, count), words['other']), True
    values = gaps if count is None else {**gaps, 'count': str(count)}
    named = {name for _, name, _, _ in Formatter().parse(sentence) if name is not None}
    # A family's sentence may leave out the count.
    unknown = values.keys() - named - ({'count'} if counted else set())
    if missing := named - values.keys():
        raise TypeError(f'{key!r} in {language!r} is missing the gaps {sorted(missing)}')
    if unknown:
        raise TypeError(f'{key!r} in {language!r} has no gaps {sorted(unknown)}')
    return sentence.format(**values)
