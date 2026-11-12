import type { FeatureCore } from "#lib/call/feature-port.ts";
import { featureView } from "#lib/ui/chat.svelte.ts";
import { words } from "#lib/words/de.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import "./skat.css";
import SkatTable from "./SkatTable.svelte";
import { Table, type TableFrame } from "./table.svelte.ts";
import { SKAT_REFUSAL, SKAT_SIGNAL } from "./wire.ts";

const CARDS_PATHS =
    '<rect x="4" y="5" width="11" height="15" rx="2.4"/><path d="M9 5.6 15 3.8a2.2 2.2 0 0 1 2.8 1.5l2.4 9.2a2.2 2.2 0 0 1-1.5 2.7L17 17.7"/>';
const MORE_ITEM_MARKUP = `<button id="skatShowBtn" class="mi" type="button" aria-expanded="false" aria-controls="skatForm"><svg class="icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CARDS_PATHS}</svg><span class="grow">${words["skat.menuTitle"]}<small>${words["skat.menuHint"]}</small></span></button>
<form id="skatForm" class="skat-form" hidden><input id="skatLink" type="url" name="link" placeholder="${words["skat.linkLabel"]}" aria-label="${words["skat.linkLabel"]}" required autocomplete="off" /><button class="button button-primary" type="submit">${words["skat.showButton"]}</button></form>`;

interface SeatFrame {
    participant_id: string;
    is_host: boolean;
}

interface RefusedFrame {
    reason: string;
}

const REFUSED_TEXT: Partial<Record<string, string>> = {
    [SKAT_REFUSAL.BAD_LINK]: words["skat.badLinkToast"],
    [SKAT_REFUSAL.BUSY]: words["skat.busyToast"],
    [SKAT_REFUSAL.NOT_OPENER_OR_HOST]: words["skat.notRemovedToast"],
};

export function install(core: FeatureCore): Promise<void> {
    const fragment = document.createElement("template");
    fragment.innerHTML = MORE_ITEM_MARKUP;
    const item = document.createElement("div");
    item.id = "skatItem";
    item.append(fragment.content);
    const showButton = item.querySelector("#skatShowBtn") as HTMLButtonElement;
    const form = item.querySelector("#skatForm") as HTMLFormElement;
    const input = item.querySelector("#skatLink") as HTMLInputElement;

    const closeForm = () => {
        form.hidden = true;
        showButton.setAttribute("aria-expanded", "false");
        input.value = "";
    };

    const table = new Table(
        {
            show: (src, shown) => {
                item.hidden = true;
                closeForm();
                core.showStage(featureView(SkatTable, { src, table: shown }));
            },
            hide: () => {
                item.hidden = false;
                core.showStage(null);
            },
        },
        (frame) => {
            core.send(frame);
        },
    );

    showButton.addEventListener("click", () => {
        form.hidden = !form.hidden;
        showButton.setAttribute("aria-expanded", String(!form.hidden));
        if (!form.hidden) input.focus();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        core.send({ type: SKAT_SIGNAL.SKAT_ATTACH, link: input.value.trim() });
        closeForm();
        core.closeMore();
    });
    core.addMoreItems(item);

    const seated = (frame: SeatFrame) => {
        table.seated(frame.participant_id, frame.is_host);
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(SKAT_SIGNAL.SKAT_TABLE, (frame: TableFrame) => {
        table.receive(frame);
    });
    core.on(SKAT_SIGNAL.SKAT_REFUSED, (frame: RefusedFrame) => {
        const text = REFUSED_TEXT[frame.reason];
        if (text !== undefined) core.showToast(text, core.toastKind.LOCKED, null);
    });
    core.onCallEnded(() => {
        table.reset();
        item.hidden = false;
        closeForm();
    });
    return Promise.resolve();
}
