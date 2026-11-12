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
    'end.callEndedTitle',
    'end.endedByHostNamedText',
    'end.endedByHostUnnamedText',
    'end.hostGoneNamedText',
    'end.hostGoneUnnamedText',
    'end.leftTitle',
    'end.leftText',
    'end.expiredTitle',
    'end.expiredText',
    'end.lockedTitle',
    'end.lockedText',
    'end.fullTitle',
    'end.fullPill',
    'end.fullText',
    'end.tooManyCallsTitle',
    'end.tooManyCallsText',
    'home.accountButton',
    'home.greetingUnnamed',
    'home.greetingNamed',
    'home.invitation',
    'home.startButton',
    'prejoin.accountButton',
    'prejoin.backButton',
    'prejoin.muteButton',
    'prejoin.cameraOffButton',
    'prejoin.unopenedHeading',
    'prejoin.hostTitle',
    'prejoin.hostHeading',
    'prejoin.hostSubLine',
    'prejoin.hostStartCameraAndMicrophone',
    'prejoin.hostStartCameraOnly',
    'prejoin.hostStartMicrophoneOnly',
    'prejoin.hostStartWithoutDevices',
    'prejoin.guestTitle',
    'prejoin.guestHeading',
    'prejoin.guestJoinCameraAndMicrophone',
    'prejoin.guestJoinCameraOnly',
    'prejoin.guestJoinMicrophoneOnly',
    'prejoin.guestJoinWithoutDevices',
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
        'end.callEndedTitle': 'Der Anruf ist beendet',
        'end.endedByHostNamedText': '{name} hat den Anruf für alle beendet.',
        'end.endedByHostUnnamedText': 'Der Host hat den Anruf für alle beendet.',
        'end.hostGoneNamedText': '{name} ist nicht innerhalb von {seconds} Sekunden zurückgekommen, deshalb wurde der Anruf für alle beendet.',
        'end.hostGoneUnnamedText': 'Der Host ist nicht innerhalb von {seconds} Sekunden zurückgekommen, deshalb wurde der Anruf für alle beendet.',
        'end.leftTitle': 'Du hast den Anruf verlassen',
        'end.leftText': 'Die anderen sprechen noch.',
        'end.expiredTitle': 'Dieser Link ist abgelaufen',
        'end.expiredText': 'Der Anruf ist vorbei. Bitte um einen neuen Link.',
        'end.lockedTitle': 'Der Anruf ist gesperrt',
        'end.lockedText': 'Der Host lässt gerade niemanden Neues herein. Versuch es gleich noch einmal.',
        'end.fullTitle': 'Der Anruf ist voll',
        'end.fullPill': '{capacity}/{capacity}',
        'end.fullText': 'In einem Anruf ist Platz für {capacity} Personen.',
        'end.tooManyCallsTitle': 'Zu viele offene Anrufe',
        'end.tooManyCallsText': 'Dein Konto ist schon in {limit} Anrufen. Verlass einen, um diesem beizutreten.',
        'home.accountButton': 'Konto',
        'home.greetingUnnamed': 'Hallo',
        'home.greetingNamed': 'Hallo, {name}',
        'home.invitation': 'Starte einen Anruf und schick den Link dann an die Leute, die du dabeihaben möchtest.',
        'home.startButton': 'Anruf starten',
        'prejoin.accountButton': 'Konto',
        'prejoin.backButton': 'Zurück',
        'prejoin.muteButton': 'Stummschalten',
        'prejoin.cameraOffButton': 'Kamera ausschalten',
        'prejoin.unopenedHeading': 'HopIn',
        'prejoin.hostTitle': 'Neuer Anruf',
        'prejoin.hostHeading': 'Bereit zum Starten?',
        'prejoin.hostSubLine': 'Du bekommst einen Link, mit dem du andere einladen kannst.',
        'prejoin.hostStartCameraAndMicrophone': 'Mit Kamera und Mikrofon starten',
        'prejoin.hostStartCameraOnly': 'Nur mit Kamera starten',
        'prejoin.hostStartMicrophoneOnly': 'Nur mit Mikrofon starten',
        'prejoin.hostStartWithoutDevices': 'Ohne Kamera und Mikrofon starten',
        'prejoin.guestTitle': 'Anruf beitreten',
        'prejoin.guestHeading': 'Bereit zum Beitreten?',
        'prejoin.guestJoinCameraAndMicrophone': 'Mit Kamera und Mikrofon beitreten',
        'prejoin.guestJoinCameraOnly': 'Nur mit Kamera beitreten',
        'prejoin.guestJoinMicrophoneOnly': 'Nur mit Mikrofon beitreten',
        'prejoin.guestJoinWithoutDevices': 'Ohne Kamera und Mikrofon beitreten',
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
