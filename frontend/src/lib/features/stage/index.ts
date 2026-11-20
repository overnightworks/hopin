import type { FeatureCore } from "#lib/call/feature-port.ts";
import { featureView } from "#lib/ui/chat.svelte.ts";
import { words } from "#lib/words.ts";
import { SIGNAL_TYPE, type ServerFrameByType } from "#lib/wire.ts";
import "./stage.css";
import StageTable from "./StageTable.svelte";
import { Table } from "./table.svelte.ts";
import { STAGE_REFUSAL, STAGE_SIGNAL, type AppEntry, type StageFrameByType } from "./wire.ts";

const CARDS_PATHS =
    '<rect x="4" y="5" width="11" height="15" rx="2.4"/><path d="M9 5.6 15 3.8a2.2 2.2 0 0 1 2.8 1.5l2.4 9.2a2.2 2.2 0 0 1-1.5 2.7L17 17.7"/>';
const ITEM_MARKUP = (name: string) =>
    `<button id="stageShowBtn-${name}" class="mi" type="button" aria-expanded="false" aria-controls="stageForm-${name}"><svg class="icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CARDS_PATHS}</svg><span class="grow"><small>${words["stage.menuHint"]}</small></span></button>
<form id="stageForm-${name}" class="stage-form" hidden><input id="stageLink-${name}" type="url" name="link" required autocomplete="off" /><button class="button button-primary" type="submit">${words["stage.showButton"]}</button></form>`;

type SeatFrame = ServerFrameByType[SIGNAL_TYPE.ROOM_CREATED] | ServerFrameByType[SIGNAL_TYPE.EXISTING_PARTICIPANTS];

type AppItem = { element: HTMLElement; close: () => void };

/** The Mehr item of one app: a button that opens a field for the link of a table of that app; `close` shuts the field
 * and clears it. */
function appItem(app: AppEntry, onLink: (title: string, link: string) => void, afterLink: () => void): AppItem {
    const fragment = document.createElement("template");
    fragment.innerHTML = ITEM_MARKUP(app.name);
    const item = document.createElement("div");
    item.append(fragment.content);
    const showButton = item.querySelector("button") as HTMLButtonElement;
    const form = item.querySelector("form") as HTMLFormElement;
    const input = item.querySelector("input") as HTMLInputElement;
    const label = words["stage.linkLabel"]({ title: app.title });
    showButton.querySelector(".grow")?.prepend(words["stage.menuTitle"]({ title: app.title }));
    input.placeholder = label;
    input.setAttribute("aria-label", label);
    const close = () => {
        form.hidden = true;
        showButton.setAttribute("aria-expanded", "false");
        input.value = "";
    };
    showButton.addEventListener("click", () => {
        form.hidden = !form.hidden;
        showButton.setAttribute("aria-expanded", String(!form.hidden));
        if (!form.hidden) input.focus();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        onLink(app.title, input.value.trim());
        close();
        afterLink();
    });
    return { element: item, close };
}

/** What a refusal says; `linkTitle` is the app whose field the person last pasted into, `shownTitle` the app on the
 * stage now. */
function refusedText(reason: STAGE_REFUSAL, linkTitle: string, shownTitle: string): string | undefined {
    switch (reason) {
        case STAGE_REFUSAL.BAD_LINK:
            return words["stage.badLinkToast"]({ title: linkTitle });
        case STAGE_REFUSAL.BUSY:
            return words["stage.busyToast"]({ title: shownTitle });
        case STAGE_REFUSAL.NOT_OPENER_OR_HOST:
            return words["stage.notRemovedToast"];
        default:
            return undefined;
    }
}

export function install(core: FeatureCore<StageFrameByType>): Promise<void> {
    const items = document.createElement("div");
    items.id = "stageItem";
    let titles = new Map<string, string>();
    let linkTitle = "";
    let appItems: AppItem[] = [];
    const closeForms = () => {
        appItems.forEach((entry) => {
            entry.close();
        });
    };

    const titleOf = (name: string | null) => (name === null ? "" : (titles.get(name) ?? name));

    const table = new Table(
        {
            show: (src, shown) => {
                items.hidden = true;
                closeForms();
                core.showStage(featureView(StageTable, { src, table: shown, title: titleOf(shown.app) }));
            },
            hide: () => {
                items.hidden = false;
                core.showStage(null);
            },
        },
        (frame) => {
            core.send(frame);
        },
    );

    core.addMoreItems(items);

    const seated = (frame: SeatFrame) => {
        table.seated(frame.participant_id, frame.is_host);
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(STAGE_SIGNAL.STAGE_APPS, (frame) => {
        titles = new Map(frame.apps.map((app) => [app.name, app.title]));
        appItems = frame.apps.map((app) =>
            appItem(
                app,
                (title, link) => {
                    linkTitle = title;
                    core.send({ type: STAGE_SIGNAL.STAGE_ATTACH, link });
                },
                () => {
                    core.closeMore();
                },
            ),
        );
        items.replaceChildren(...appItems.map((entry) => entry.element));
    });
    core.on(STAGE_SIGNAL.STAGE_TABLE, (frame) => {
        table.receive(frame);
    });
    core.on(STAGE_SIGNAL.STAGE_REFUSED, (frame) => {
        const text = refusedText(frame.reason, linkTitle, titleOf(table.app));
        if (text !== undefined) core.showToast(text, core.toastKind.LOCKED, null);
    });
    core.onCallEnded(() => {
        table.reset();
        items.hidden = false;
        closeForms();
    });
    return Promise.resolve();
}
