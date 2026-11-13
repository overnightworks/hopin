// @vitest-environment jsdom
import { mount, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";
import type { FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import { words } from "#lib/words/de.ts";
import { TOAST_KIND } from "#lib/ui/toast.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { install } from "./index.ts";
import { SKAT_REFUSAL, SKAT_SIGNAL } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

async function installedSkat() {
    const handlers = new Map<string, (frame: never) => void>();
    const sent: unknown[] = [];
    const toasts: string[] = [];
    const stage: (FeatureView | null)[] = [];
    const closed: boolean[] = [];
    const moreItems: Element[] = [];
    const callEndedHooks: (() => void)[] = [];
    const core: FeatureCore = {
        on: (type, handler) => void handlers.set(type, handler),
        send: (frame) => void sent.push(frame),
        isConnected: () => true,
        showToast: (text) => void toasts.push(text),
        toastKind: TOAST_KIND,
        showAgentTile: () => undefined,
        showStage: (view) => void stage.push(view),
        drawAgentFigures: () => undefined,
        closeMore: () => void closed.push(true),
        addMoreItems: (...items) => void moreItems.push(...items),
        receiveChatLine: () => undefined,
        pinLastChatLine: () => undefined,
        showChatCard: () => undefined,
        onCallEnded: (hook) => void callEndedHooks.push(hook),
        onMicrophone: () => undefined,
    };
    await install(core);
    const [item] = moreItems as [HTMLElement];
    const emit = (type: string, frame: object) => {
        const handler = handlers.get(type);
        if (handler === undefined) throw new Error(`no handler for ${type}`);
        (handler as (frame: object) => void)(frame);
    };
    const query = (selector: string) => {
        const found = item.querySelector(selector);
        if (found === null) throw new Error(`no ${selector}`);
        return found;
    };
    const button = () => query("#skatShowBtn") as HTMLButtonElement;
    const form = () => query("#skatForm") as HTMLFormElement;
    const input = () => query("#skatLink") as HTMLInputElement;
    return {
        item,
        sent,
        toasts,
        stage,
        closed,
        emit,
        button,
        form,
        input,
        endCall: () => {
            callEndedHooks.forEach((hook) => {
                hook();
            });
        },
        paste: (link: string) => {
            button().click();
            input().value = ` ${link} `;
            form().dispatchEvent(new Event("submit", { cancelable: true }));
        },
    };
}

const SHOWN = {
    revision: 2,
    attachment: 2,
    table: "a".repeat(32),
    src: "https://skat.example/t/a?mode=call",
    opener_id: "me",
};
const NONE = { revision: 3, attachment: null, table: null, src: null, opener_id: null };

describe("the Skat entry in More", () => {
    it("opens a link field on a tap, and a second tap closes it", async () => {
        const page = await installedSkat();
        page.button().click();
        const opened = [page.form().hidden, page.button().getAttribute("aria-expanded")];
        page.button().click();

        expect([opened, page.form().hidden]).toEqual([[false, "true"], true]);
    });

    it("sends the pasted link without its blanks and closes the field", async () => {
        const page = await installedSkat();

        page.paste("https://skat.example/t/a");

        expect(page.sent).toEqual([{ type: SKAT_SIGNAL.SKAT_ATTACH, link: "https://skat.example/t/a" }]);
        expect(page.form().hidden).toBe(true);
        expect(page.input().value).toBe("");
        expect(page.closed).toEqual([true]);
    });

    it("is hidden while a table is shown and comes back when it is removed", async () => {
        const page = await installedSkat();

        page.emit(SKAT_SIGNAL.SKAT_TABLE, SHOWN);
        const whileShown = page.item.hidden;
        page.emit(SKAT_SIGNAL.SKAT_TABLE, NONE);

        expect([whileShown, page.item.hidden]).toEqual([true, false]);
    });
});

describe("the stage", () => {
    it("shows a view for a table and takes it away when the table goes", async () => {
        const page = await installedSkat();
        page.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: "me", is_host: false });

        page.emit(SKAT_SIGNAL.SKAT_TABLE, SHOWN);
        page.emit(SKAT_SIGNAL.SKAT_TABLE, NONE);

        expect(page.stage.map((view) => view !== null)).toEqual([true, false]);
    });

    it("frames the table in the agreed sandbox and sends its removal from the opener's button", async () => {
        const page = await installedSkat();
        page.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: "me", is_host: false });
        page.emit(SKAT_SIGNAL.SKAT_TABLE, SHOWN);
        const [view] = page.stage;
        const target = document.createElement("div");
        const mounted = mount(view as FeatureView, { target });

        const frame = target.querySelector("iframe");
        target.querySelector("button")?.click();
        void unmount(mounted);

        expect([frame?.getAttribute("sandbox"), frame?.getAttribute("allow"), frame?.getAttribute("src")]).toEqual([
            "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox",
            "",
            SHOWN.src,
        ]);
        expect(frame?.getAttribute("referrerpolicy")).toBe("no-referrer");
        expect(page.sent).toEqual([{ type: SKAT_SIGNAL.SKAT_DETACH, attachment: 2 }]);
    });

    it("offers no removal to a seat that may not remove the table", async () => {
        const page = await installedSkat();
        page.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: "me", is_host: false });
        page.emit(SKAT_SIGNAL.SKAT_TABLE, { ...SHOWN, opener_id: "someone" });
        const [view] = page.stage;
        const target = document.createElement("div");

        const mounted = mount(view as FeatureView, { target });
        const offered = target.querySelector("button");
        void unmount(mounted);

        expect(offered).toBeNull();
    });

    it("is cleared when the call ends", async () => {
        const page = await installedSkat();
        page.emit(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participant_id: "me", is_host: true });
        page.emit(SKAT_SIGNAL.SKAT_TABLE, SHOWN);

        page.endCall();

        expect(page.stage.map((view) => view !== null)).toEqual([true, false]);
        expect(page.item.hidden).toBe(false);
    });
});

describe("a refusal", () => {
    it.each([
        [SKAT_REFUSAL.BAD_LINK, words["skat.badLinkToast"]],
        [SKAT_REFUSAL.BUSY, words["skat.busyToast"]],
        [SKAT_REFUSAL.NOT_OPENER_OR_HOST, words["skat.notRemovedToast"]],
    ])("%s is told to the person", async (reason, text) => {
        const page = await installedSkat();

        page.emit(SKAT_SIGNAL.SKAT_REFUSED, { reason });

        expect(page.toasts).toEqual([text]);
    });

    it("of a kind this page does not know is not toasted", async () => {
        const page = await installedSkat();

        page.emit(SKAT_SIGNAL.SKAT_REFUSED, { reason: "something_new" });

        expect(page.toasts).toEqual([]);
    });
});
