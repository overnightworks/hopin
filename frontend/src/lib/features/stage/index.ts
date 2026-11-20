import type { FeatureCore } from "#lib/call/feature-port.ts";
import { featureView } from "#lib/ui/chat.svelte.ts";
import { words } from "#lib/words.ts";
import { SIGNAL_TYPE, type ServerFrameByType } from "#lib/wire.ts";
import "./stage.css";
import StageTable from "./StageTable.svelte";
import { Table } from "./table.svelte.ts";
import { STAGE_REFUSAL, STAGE_SIGNAL, type StageFrameByType } from "./wire.ts";

const CARDS_PATHS =
    '<rect x="4" y="5" width="11" height="15" rx="2.4"/><path d="M9 5.6 15 3.8a2.2 2.2 0 0 1 2.8 1.5l2.4 9.2a2.2 2.2 0 0 1-1.5 2.7L17 17.7"/>';
const MORE_ITEM_MARKUP = `<button id="stageShowBtn" class="mi" type="button" aria-expanded="false" aria-controls="stageForm"><svg class="icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CARDS_PATHS}</svg><span class="grow">${words["skat.menuTitle"]}<small>${words["skat.menuHint"]}</small></span></button>
<form id="stageForm" class="stage-form" hidden><input id="stageLink" type="url" name="link" placeholder="${words["skat.linkLabel"]}" aria-label="${words["skat.linkLabel"]}" required autocomplete="off" /><button class="button button-primary" type="submit">${words["skat.showButton"]}</button></form>`;

type SeatFrame = ServerFrameByType[SIGNAL_TYPE.ROOM_CREATED] | ServerFrameByType[SIGNAL_TYPE.EXISTING_PARTICIPANTS];

const REFUSED_TEXT: Partial<Record<STAGE_REFUSAL, string>> = {
    [STAGE_REFUSAL.BAD_LINK]: words["skat.badLinkToast"],
    [STAGE_REFUSAL.BUSY]: words["skat.busyToast"],
    [STAGE_REFUSAL.NOT_OPENER_OR_HOST]: words["skat.notRemovedToast"],
};

export function install(core: FeatureCore<StageFrameByType>): Promise<void> {
    const fragment = document.createElement("template");
    fragment.innerHTML = MORE_ITEM_MARKUP;
    const item = document.createElement("div");
    item.id = "stageItem";
    item.append(fragment.content);
    const showButton = item.querySelector("#stageShowBtn") as HTMLButtonElement;
    const form = item.querySelector("#stageForm") as HTMLFormElement;
    const input = item.querySelector("#stageLink") as HTMLInputElement;

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
                core.showStage(featureView(StageTable, { src, table: shown }));
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
        core.send({ type: STAGE_SIGNAL.STAGE_ATTACH, link: input.value.trim() });
        closeForm();
        core.closeMore();
    });
    core.addMoreItems(item);

    const seated = (frame: SeatFrame) => {
        table.seated(frame.participant_id, frame.is_host);
    };
    core.on(SIGNAL_TYPE.ROOM_CREATED, seated);
    core.on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, seated);
    core.on(STAGE_SIGNAL.STAGE_TABLE, (frame) => {
        table.receive(frame);
    });
    core.on(STAGE_SIGNAL.STAGE_REFUSED, (frame) => {
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
