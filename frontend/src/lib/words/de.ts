// Generated from the de catalog by design-catalogs. Do not edit.

export const language = "de";
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
    readonly "invite.menuLabel": string;
    readonly "invite.heading": string;
    readonly "invite.closeButton": string;
    readonly "invite.copyButton": string;
    readonly "invite.membersNote": string;
    readonly "invite.copyLinkButton": string;
    readonly "invite.shareButton": string;
    readonly "chat.header": string;
    readonly "chat.inputPlaceholder": string;
    readonly "chat.inputLabel": string;
    readonly "chat.sendButton": string;
    readonly "account.menuLabel": string;
    readonly "tile.removeButton": string;
    readonly "tile.aiBadge": string;
    readonly "tile.agentThinkingLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentWaitingLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentSpeakingLabel": (gaps: { readonly name: string }) => string;
    readonly "tile.agentPresentLabel": (gaps: { readonly name: string }) => string;
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
    "invite.menuLabel": "Leute einladen",
    "invite.heading": "Leute einladen",
    "invite.closeButton": "Schließen",
    "invite.copyButton": "Kopieren",
    "invite.membersNote": "Nur HopIn-Mitglieder können beitreten.",
    "invite.copyLinkButton": "Link kopieren",
    "invite.shareButton": "Teilen",
    "chat.header": "Chat",
    "chat.inputPlaceholder": "Schreib eine Nachricht …",
    "chat.inputLabel": "Chatnachricht",
    "chat.sendButton": "Senden",
    "account.menuLabel": "Konto",
    "tile.removeButton": "Entfernen",
    "tile.aiBadge": "KI",
    "tile.agentThinkingLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, denkt nach",
    "tile.agentWaitingLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, wartet auf Antworten",
    "tile.agentSpeakingLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, spricht",
    "tile.agentPresentLabel": (gaps: { readonly name: string }) => gaps.name + ", KI, ist im Anruf",
};
export type Key = keyof Words;
