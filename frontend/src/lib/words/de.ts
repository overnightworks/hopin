// Generated from the de catalog by design-catalogs. Do not edit.

export const language = "de";
const numberFormat = new Intl.NumberFormat("de");
const pluralRules = new Intl.PluralRules("de");
// prettier-ignore
export type Words = {
    readonly "call.unexpectedError": string;
    readonly "call.pageTitle": string;
    readonly "call.brandName": string;
    readonly "call.leaveQuestion": string;
    readonly "call.leaveCancelButton": string;
    readonly "call.leaveConfirmButton": string;
    readonly "call.lockedTag": string;
    readonly "call.participantCountLabel": string;
    readonly "call.ownTileName": string;
    readonly "call.waitingTitle": string;
    readonly "call.waitingText": string;
    readonly "call.waitingCopyLinkButton": string;
    readonly "call.waitingShareButton": string;
    readonly "call.controlBarLabel": string;
    readonly "call.muteButton": string;
    readonly "call.cameraOffButton": string;
    readonly "call.shareScreenButton": string;
    readonly "call.inviteButton": string;
    readonly "call.chatButton": string;
    readonly "call.moreButton": string;
    readonly "call.leaveButton": string;
    readonly "call.statusNotConnected": string;
    readonly "call.statusJoining": string;
    readonly "call.statusInCall": string;
    readonly "call.statusReconnecting": (count: number) => string;
    readonly "call.statusConnectionLost": string;
    readonly "call.statusDisconnected": string;
    readonly "call.statusConnectionError": string;
    readonly "call.statusCameraMissing": (gaps: { readonly errorName: string }) => string;
    readonly "call.statusScreenShareFailed": (gaps: { readonly errorName: string }) => string;
    readonly "call.statusIceServersFailed": (gaps: { readonly status: string }) => string;
    readonly "call.addOnLoadFailedToast": string;
    readonly "end.accountButton": string;
    readonly "end.retryButton": string;
    readonly "end.homeButton": string;
    readonly "end.rejoinButton": string;
    readonly "end.callEndedTitle": string;
    readonly "end.endedByHostNamedText": (gaps: { readonly name: string }) => string;
    readonly "end.endedByHostUnnamedText": string;
    readonly "end.hostGoneNamedText": (gaps: { readonly name: string; readonly seconds: string }) => string;
    readonly "end.hostGoneUnnamedText": (gaps: { readonly seconds: string }) => string;
    readonly "end.leftTitle": string;
    readonly "end.leftText": string;
    readonly "end.expiredTitle": string;
    readonly "end.expiredText": string;
    readonly "end.lockedTitle": string;
    readonly "end.lockedText": string;
    readonly "end.fullTitle": string;
    readonly "end.fullPill": (gaps: { readonly capacity: string }) => string;
    readonly "end.fullText": (gaps: { readonly capacity: string }) => string;
    readonly "end.tooManyCallsTitle": string;
    readonly "end.tooManyCallsText": (gaps: { readonly limit: string }) => string;
    readonly "home.accountButton": string;
    readonly "home.greetingUnnamed": string;
    readonly "home.greetingNamed": (gaps: { readonly name: string }) => string;
    readonly "home.invitation": string;
    readonly "home.startButton": string;
    readonly "prejoin.accountButton": string;
    readonly "prejoin.backButton": string;
    readonly "prejoin.muteButton": string;
    readonly "prejoin.cameraOffButton": string;
    readonly "prejoin.unopenedHeading": string;
    readonly "prejoin.hostTitle": string;
    readonly "prejoin.hostHeading": string;
    readonly "prejoin.hostSubLine": string;
    readonly "prejoin.hostStartCameraAndMicrophone": string;
    readonly "prejoin.hostStartCameraOnly": string;
    readonly "prejoin.hostStartMicrophoneOnly": string;
    readonly "prejoin.hostStartWithoutDevices": string;
    readonly "prejoin.guestTitle": string;
    readonly "prejoin.guestHeading": string;
    readonly "prejoin.guestJoinCameraAndMicrophone": string;
    readonly "prejoin.guestJoinCameraOnly": string;
    readonly "prejoin.guestJoinMicrophoneOnly": string;
    readonly "prejoin.guestJoinWithoutDevices": string;
    readonly "prejoin.mediaRefused": (gaps: { readonly errorName: string }) => string;
    readonly "more.menuLabel": string;
    readonly "more.lockSwitch": string;
    readonly "more.lockLineLocked": string;
    readonly "more.lockLineUnlocked": string;
    readonly "more.lockedToast": string;
    readonly "more.unlockedToast": string;
    readonly "invite.menuLabel": string;
    readonly "invite.heading": string;
    readonly "invite.closeButton": string;
    readonly "invite.copyButton": string;
    readonly "invite.membersNote": string;
    readonly "invite.copyLinkButton": string;
    readonly "invite.shareButton": string;
    readonly "invite.linkCopiedToast": string;
    readonly "invite.copyFailedToast": string;
    readonly "invite.shareFailedToast": string;
    readonly "chat.header": string;
    readonly "chat.inputPlaceholder": string;
    readonly "chat.inputLabel": string;
    readonly "chat.sendButton": string;
    readonly "chat.closeButton": string;
    readonly "account.menuLabel": string;
    readonly "account.unnamedMenuName": string;
    readonly "account.unnamedRowName": string;
    readonly "signIn.logOutButton": string;
    readonly "tile.removeButton": string;
    readonly "tile.aiBadge": string;
    readonly "tile.seeSwitchLabel": string;
    readonly "tile.lookMarkLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentThinkingLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentWaitingLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentSpeakingLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentPresentLabel": (gaps: { readonly name: string }) => string;
    readonly "game.masterName": string;
    readonly "game.startButton": string;
    readonly "game.startHint": (gaps: { readonly name: string }) => string;
    readonly "game.runningLabel": string;
    readonly "game.endButton": string;
    readonly "game.thinkingButton": (gaps: { readonly name: string }) => string;
    readonly "game.thinkingLabel": (gaps: { readonly name: string }) => string;
    readonly "game.voiceLabel": (gaps: { readonly name: string }) => string;
    readonly "game.voiceOn": string;
    readonly "game.voiceOff": string;
    readonly "game.questionCardLabel": string;
    readonly "game.revealButton": string;
    readonly "game.answerLabel": string;
    readonly "game.sendButton": string;
    readonly "game.questionProgress": (gaps: { readonly round: string; readonly rounds: string }) => string;
    readonly "game.answerCountStatus": (gaps: { readonly answered: string; readonly players: string }) => string;
    readonly "game.answerSentCountStatus": (gaps: { readonly answered: string; readonly players: string }) => string;
    readonly "game.busyRetryToast": (count: number, gaps: { readonly name: string }) => string;
    readonly "game.unavailableToast": (gaps: { readonly name: string }) => string;
    readonly "game.noAccessToast": string;
    readonly "game.signInAgainToast": (gaps: { readonly name: string }) => string;
    readonly "game.signInButton": string;
    readonly "game.budgetSpentToast": string;
    readonly "game.alreadyAnsweringToast": (gaps: { readonly name: string }) => string;
    readonly "game.quizRunningToast": string;
    readonly "game.notStarterToast": string;
    readonly "game.notStarterOrHostToast": string;
    readonly "game.answerClosedToast": string;
    readonly "game.answerTooLongToast": string;
    readonly "game.endedToast": string;
    readonly "game.endedByLeaverToast": (gaps: { readonly leaver: string }) => string;
    readonly "skat.menuTitle": string;
    readonly "skat.menuHint": string;
    readonly "skat.linkLabel": string;
    readonly "skat.showButton": string;
    readonly "skat.badLinkToast": string;
    readonly "skat.busyToast": string;
    readonly "skat.notRemovedToast": string;
    readonly "skat.frameTitle": string;
    readonly "skat.removeButton": string;
    readonly "agent.notRemovedToast": string;
};
// prettier-ignore
export const words: Words = {
    "call.unexpectedError": "Etwas ist schiefgegangen. Bitte versuch es noch einmal.",
    "call.pageTitle": "HopIn - Videoanruf",
    "call.brandName": "HopIn",
    "call.leaveQuestion": "Diesen Anruf verlassen?",
    "call.leaveCancelButton": "Abbrechen",
    "call.leaveConfirmButton": "Verlassen",
    "call.lockedTag": "Gesperrt",
    "call.participantCountLabel": "Teilnehmende",
    "call.ownTileName": "Du",
    "call.waitingTitle": "Warten auf andere",
    "call.waitingText": "Lade Leute ein, diesem Anruf beizutreten.",
    "call.waitingCopyLinkButton": "Link kopieren",
    "call.waitingShareButton": "Teilen",
    "call.controlBarLabel": "Anrufsteuerung",
    "call.muteButton": "Stummschalten",
    "call.cameraOffButton": "Kamera ausschalten",
    "call.shareScreenButton": "Bildschirm teilen",
    "call.inviteButton": "Einladen",
    "call.chatButton": "Chat",
    "call.moreButton": "Mehr",
    "call.leaveButton": "Verlassen",
    "call.statusNotConnected": "Nicht verbunden",
    "call.statusJoining": "Verbunden, Beitritt läuft …",
    "call.statusInCall": "Im Anruf",
    "call.statusReconnecting": (count: number) => ({
        zero: "Neu verbinden in " + numberFormat.format(count) + " Sekunden …",
        one: "Neu verbinden in " + numberFormat.format(count) + " Sekunde …",
        two: "Neu verbinden in " + numberFormat.format(count) + " Sekunden …",
        few: "Neu verbinden in " + numberFormat.format(count) + " Sekunden …",
        many: "Neu verbinden in " + numberFormat.format(count) + " Sekunden …",
        other: "Neu verbinden in " + numberFormat.format(count) + " Sekunden …",
    })[pluralRules.select(count)],
    "call.statusConnectionLost": "Verbindung verloren",
    "call.statusDisconnected": "Getrennt",
    "call.statusConnectionError": "Verbindungsfehler",
    "call.statusCameraMissing": (gaps: { readonly errorName: string }) => "Keine Kamera gefunden (" + gaps.errorName + ")",
    "call.statusScreenShareFailed": (gaps: { readonly errorName: string }) => "Bildschirmfreigabe fehlgeschlagen (" + gaps.errorName + ")",
    "call.statusIceServersFailed": (gaps: { readonly status: string }) => "Die Server für die Verbindung konnten nicht geladen werden (" + gaps.status + ").",
    "call.addOnLoadFailedToast": "Ein Zusatzmodul konnte nicht geladen werden.",
    "end.accountButton": "Konto",
    "end.retryButton": "Erneut versuchen",
    "end.homeButton": "Zurück zum Start",
    "end.rejoinButton": "Wieder beitreten",
    "end.callEndedTitle": "Der Anruf ist beendet",
    "end.endedByHostNamedText": (gaps: { readonly name: string }) => gaps.name + " hat den Anruf für alle beendet.",
    "end.endedByHostUnnamedText": "Der Host hat den Anruf für alle beendet.",
    "end.hostGoneNamedText": (gaps: { readonly name: string; readonly seconds: string }) => gaps.name + " ist nicht innerhalb von " + gaps.seconds + " Sekunden zurückgekommen, deshalb wurde der Anruf für alle beendet.",
    "end.hostGoneUnnamedText": (gaps: { readonly seconds: string }) => "Der Host ist nicht innerhalb von " + gaps.seconds + " Sekunden zurückgekommen, deshalb wurde der Anruf für alle beendet.",
    "end.leftTitle": "Du hast den Anruf verlassen",
    "end.leftText": "Die anderen sprechen noch.",
    "end.expiredTitle": "Dieser Link ist abgelaufen",
    "end.expiredText": "Der Anruf ist vorbei. Bitte um einen neuen Link.",
    "end.lockedTitle": "Der Anruf ist gesperrt",
    "end.lockedText": "Der Host lässt gerade niemanden Neues herein. Versuch es gleich noch einmal.",
    "end.fullTitle": "Der Anruf ist voll",
    "end.fullPill": (gaps: { readonly capacity: string }) => gaps.capacity + "/" + gaps.capacity,
    "end.fullText": (gaps: { readonly capacity: string }) => "In einem Anruf ist Platz für " + gaps.capacity + " Personen.",
    "end.tooManyCallsTitle": "Zu viele offene Anrufe",
    "end.tooManyCallsText": (gaps: { readonly limit: string }) => "Dein Konto ist schon in " + gaps.limit + " Anrufen. Verlass einen, um diesem beizutreten.",
    "home.accountButton": "Konto",
    "home.greetingUnnamed": "Hallo",
    "home.greetingNamed": (gaps: { readonly name: string }) => "Hallo, " + gaps.name,
    "home.invitation": "Starte einen Anruf und schick den Link dann an die Leute, die du dabeihaben möchtest.",
    "home.startButton": "Anruf starten",
    "prejoin.accountButton": "Konto",
    "prejoin.backButton": "Zurück",
    "prejoin.muteButton": "Stummschalten",
    "prejoin.cameraOffButton": "Kamera ausschalten",
    "prejoin.unopenedHeading": "HopIn",
    "prejoin.hostTitle": "Neuer Anruf",
    "prejoin.hostHeading": "Bereit zum Starten?",
    "prejoin.hostSubLine": "Du bekommst einen Link, mit dem du andere einladen kannst.",
    "prejoin.hostStartCameraAndMicrophone": "Mit Kamera und Mikrofon starten",
    "prejoin.hostStartCameraOnly": "Nur mit Kamera starten",
    "prejoin.hostStartMicrophoneOnly": "Nur mit Mikrofon starten",
    "prejoin.hostStartWithoutDevices": "Ohne Kamera und Mikrofon starten",
    "prejoin.guestTitle": "Anruf beitreten",
    "prejoin.guestHeading": "Bereit zum Beitreten?",
    "prejoin.guestJoinCameraAndMicrophone": "Mit Kamera und Mikrofon beitreten",
    "prejoin.guestJoinCameraOnly": "Nur mit Kamera beitreten",
    "prejoin.guestJoinMicrophoneOnly": "Nur mit Mikrofon beitreten",
    "prejoin.guestJoinWithoutDevices": "Ohne Kamera und Mikrofon beitreten",
    "prejoin.mediaRefused": (gaps: { readonly errorName: string }) => "Weder Kamera noch Mikrofon verfügbar (" + gaps.errorName + "). Schließ ein Gerät an oder erlaub den Zugriff und versuch es noch einmal.",
    "more.menuLabel": "Mehr",
    "more.lockSwitch": "Anruf sperren",
    "more.lockLineLocked": "Niemand Neues kann beitreten",
    "more.lockLineUnlocked": "Neue Leute können beitreten",
    "more.lockedToast": "Anruf gesperrt",
    "more.unlockedToast": "Anruf entsperrt",
    "invite.menuLabel": "Leute einladen",
    "invite.heading": "Leute einladen",
    "invite.closeButton": "Schließen",
    "invite.copyButton": "Kopieren",
    "invite.membersNote": "Nur HopIn-Mitglieder können beitreten.",
    "invite.copyLinkButton": "Link kopieren",
    "invite.shareButton": "Teilen",
    "invite.linkCopiedToast": "Link kopiert",
    "invite.copyFailedToast": "Der Link konnte nicht kopiert werden",
    "invite.shareFailedToast": "Der Link konnte nicht geteilt werden",
    "chat.header": "Chat",
    "chat.inputPlaceholder": "Schreib eine Nachricht …",
    "chat.inputLabel": "Chatnachricht",
    "chat.sendButton": "Senden",
    "chat.closeButton": "Chat schließen",
    "account.menuLabel": "Konto",
    "account.unnamedMenuName": "Konto",
    "account.unnamedRowName": "Du",
    "signIn.logOutButton": "Abmelden",
    "tile.removeButton": "Entfernen",
    "tile.aiBadge": "KI",
    "tile.seeSwitchLabel": "Agent darf mich sehen",
    "tile.lookMarkLabel": (gaps: { readonly name: string }) => gaps.name + " schaut hin",
    "tile.agentThinkingLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, denkt nach",
    "tile.agentWaitingLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, wartet auf Antworten",
    "tile.agentSpeakingLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, spricht",
    "tile.agentPresentLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, ist im Anruf",
    "game.masterName": "Quizmaster",
    "game.startButton": "Quiz starten",
    "game.startHint": (gaps: { readonly name: string }) => gaps.name + " fragt die Runde",
    "game.runningLabel": "Quiz läuft",
    "game.endButton": "Quiz beenden",
    "game.thinkingButton": (gaps: { readonly name: string }) => gaps.name + " denkt nach …",
    "game.thinkingLabel": (gaps: { readonly name: string }) => gaps.name + " denkt nach",
    "game.voiceLabel": (gaps: { readonly name: string }) => gaps.name + "-Stimme",
    "game.voiceOn": "An",
    "game.voiceOff": "Aus",
    "game.questionCardLabel": "Quizfrage",
    "game.revealButton": "Auflösen",
    "game.answerLabel": "Deine Antwort",
    "game.sendButton": "Senden",
    "game.questionProgress": (gaps: { readonly round: string; readonly rounds: string }) => "Frage " + gaps.round + " von " + gaps.rounds,
    "game.answerCountStatus": (gaps: { readonly answered: string; readonly players: string }) => gaps.answered + " von " + gaps.players + " haben geantwortet",
    "game.answerSentCountStatus": (gaps: { readonly answered: string; readonly players: string }) => "Antwort gesendet · " + gaps.answered + " von " + gaps.players + " haben geantwortet",
    "game.busyRetryToast": (count: number, gaps: { readonly name: string }) => ({
        zero: gaps.name + " ist gerade beschäftigt. Versuch es in " + numberFormat.format(count) + " Sekunden noch einmal.",
        one: gaps.name + " ist gerade beschäftigt. Versuch es in " + numberFormat.format(count) + " Sekunde noch einmal.",
        two: gaps.name + " ist gerade beschäftigt. Versuch es in " + numberFormat.format(count) + " Sekunden noch einmal.",
        few: gaps.name + " ist gerade beschäftigt. Versuch es in " + numberFormat.format(count) + " Sekunden noch einmal.",
        many: gaps.name + " ist gerade beschäftigt. Versuch es in " + numberFormat.format(count) + " Sekunden noch einmal.",
        other: gaps.name + " ist gerade beschäftigt. Versuch es in " + numberFormat.format(count) + " Sekunden noch einmal.",
    })[pluralRules.select(count)],
    "game.unavailableToast": (gaps: { readonly name: string }) => gaps.name + " ist gerade nicht erreichbar.",
    "game.noAccessToast": "Dein Konto hat keinen KI-Zugang.",
    "game.signInAgainToast": (gaps: { readonly name: string }) => "Melde dich noch einmal an, um " + gaps.name + " zu nutzen.",
    "game.signInButton": "Anmelden",
    "game.budgetSpentToast": "Dein KI-Budget ist vorerst aufgebraucht.",
    "game.alreadyAnsweringToast": (gaps: { readonly name: string }) => gaps.name + " antwortet schon.",
    "game.quizRunningToast": "Es läuft schon ein Quiz.",
    "game.notStarterToast": "Nur wer das Quiz gestartet hat, kann auflösen.",
    "game.notStarterOrHostToast": "Nur wer das Quiz gestartet hat oder der Host kann es beenden.",
    "game.answerClosedToast": "Diese Frage ist schon abgeschlossen.",
    "game.answerTooLongToast": "Deine Antwort ist zu lang. Kürz sie und schick sie noch einmal.",
    "game.endedToast": "Quiz beendet.",
    "game.endedByLeaverToast": (gaps: { readonly leaver: string }) => "Quiz beendet: " + gaps.leaver + " ist gegangen.",
    "skat.menuTitle": "Skat-Tisch zeigen",
    "skat.menuHint": "Link eines Tisches einfügen",
    "skat.linkLabel": "Link des Skat-Tisches",
    "skat.showButton": "Zeigen",
    "skat.badLinkToast": "Das ist kein Link zu einem Skat-Tisch.",
    "skat.busyToast": "Auf der Bühne steht schon ein Skat-Tisch.",
    "skat.notRemovedToast": "Nur wer den Tisch mitgebracht hat oder der Host kann ihn entfernen.",
    "skat.frameTitle": "Skat-Tisch",
    "skat.removeButton": "Tisch entfernen",
    "agent.notRemovedToast": "Nur wer die KI geholt hat oder der Host kann sie entfernen.",
};
export type Key = keyof Words;
