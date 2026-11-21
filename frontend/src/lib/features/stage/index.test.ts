// @vitest-environment jsdom
import { mount, unmount } from "svelte";
import { describe, expect, it, vi } from "vitest";
import type { FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import type { StartLink } from "#lib/ui/navigation.ts";
import { words } from "#lib/words.ts";
import { TOAST_KIND } from "#lib/ui/toast.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { install } from "./index.ts";
import { STAGE_REFUSAL, STAGE_SIGNAL } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

const APPS = {
    apps: [
        { name: "skat", title: "Skat" },
        { name: "chess", title: "Schach" },
    ],
};

async function installedStage(startLink: StartLink | null = null) {
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
        onAgentFigureEnded: () => undefined,
        gestureAgentFigure: () => undefined,
        speakAgentFigure: () => undefined,
        thinkAgentFigure: () => undefined,
        closeMore: () => void closed.push(true),
        addMoreItems: (...items) => void moreItems.push(...items),
        receiveChatLine: () => undefined,
        pinLastChatLine: () => undefined,
        showChatCard: () => undefined,
        setLineReaders: () => undefined,
        onCallEnded: (hook) => void callEndedHooks.push(hook),
        startLink,
        onMicrophone: () => undefined,
        takePicture: () => Promise.resolve(null),
    };
    await install(core);
    const [item] = moreItems as [HTMLElement];
    const emit = (type: string, frame: object) => {
        const handler = handlers.get(type);
        if (handler === undefined) throw new Error(`no handler for ${type}`);
        (handler as (frame: object) => void)(frame);
    };
    emit(STAGE_SIGNAL.STAGE_APPS, APPS);
    const query = (selector: string) => {
        const found = item.querySelector(selector);
        if (found === null) throw new Error(`no ${selector}`);
        return found;
    };
    let app = "skat";
    const button = () => query(`#stageShowBtn-${app}`) as HTMLButtonElement;
    const form = () => query(`#stageForm-${app}`) as HTMLFormElement;
    const input = () => query(`#stageLink-${app}`) as HTMLInputElement;
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
        use: (name: string) => {
            app = name;
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
    app: "skat",
    table: "a".repeat(32),
    src: "https://skat.example/t/a?mode=call",
    opener_id: "me",
};
const NONE = { revision: 3, attachment: null, app: null, table: null, src: null, opener_id: null };

describe("the entries in More", () => {
    it("offers one entry per registered app, named by its title", async () => {
        const page = await installedStage();

        const labels = [...page.item.querySelectorAll("button.mi .grow")].map((label) => label.firstChild?.textContent);

        expect(labels).toEqual([
            words["stage.menuTitle"]({ title: "Skat" }),
            words["stage.menuTitle"]({ title: "Schach" }),
        ]);
    });

    it("takes a title for text, never for markup", async () => {
        const page = await installedStage();

        page.emit(STAGE_SIGNAL.STAGE_APPS, { apps: [{ name: "odd", title: "<img src=x>" }] });

        expect(page.item.querySelector("img")).toBeNull();
    });

    it("opens a link field on a tap, and a second tap closes it", async () => {
        const page = await installedStage();
        page.button().click();
        const opened = [page.form().hidden, page.button().getAttribute("aria-expanded")];
        page.button().click();

        expect([opened, page.form().hidden]).toEqual([[false, "true"], true]);
    });

    it("sends the pasted link without its blanks and closes the field", async () => {
        const page = await installedStage();

        page.paste("https://skat.example/t/a");

        expect(page.sent).toEqual([{ type: STAGE_SIGNAL.STAGE_ATTACH, link: "https://skat.example/t/a" }]);
        expect(page.form().hidden).toBe(true);
        expect(page.input().value).toBe("");
        expect(page.closed).toEqual([true]);
    });

    it("is hidden while a table is shown and comes back when it is removed", async () => {
        const page = await installedStage();

        page.emit(STAGE_SIGNAL.STAGE_TABLE, SHOWN);
        const whileShown = page.item.hidden;
        page.emit(STAGE_SIGNAL.STAGE_TABLE, NONE);

        expect([whileShown, page.item.hidden]).toEqual([true, false]);
    });
});

describe("an open link field", () => {
    const closedAndEmpty = (page: Awaited<ReturnType<typeof installedStage>>) => [
        page.form().hidden,
        page.button().getAttribute("aria-expanded"),
        page.input().value,
    ];
    const openWithText = (page: Awaited<ReturnType<typeof installedStage>>) => {
        page.button().click();
        page.input().value = "https://skat.example/t/a";
    };

    it("is closed and emptied when a table is shown by another seat", async () => {
        const page = await installedStage();
        openWithText(page);

        page.emit(STAGE_SIGNAL.STAGE_TABLE, SHOWN);
        page.emit(STAGE_SIGNAL.STAGE_TABLE, NONE);

        expect(closedAndEmpty(page)).toEqual([true, "false", ""]);
    });

    it("is closed and emptied when the call ends", async () => {
        const page = await installedStage();
        openWithText(page);

        page.endCall();

        expect(closedAndEmpty(page)).toEqual([true, "false", ""]);
    });
});

describe("the stage", () => {
    it("shows a view for a table and takes it away when the table goes", async () => {
        const page = await installedStage();
        page.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: "me", is_host: false });

        page.emit(STAGE_SIGNAL.STAGE_TABLE, SHOWN);
        page.emit(STAGE_SIGNAL.STAGE_TABLE, NONE);

        expect(page.stage.map((view) => view !== null)).toEqual([true, false]);
    });

    it("frames the table in the agreed sandbox and sends its removal from the opener's button", async () => {
        const page = await installedStage();
        page.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: "me", is_host: false });
        page.emit(STAGE_SIGNAL.STAGE_TABLE, SHOWN);
        const [view] = page.stage;
        const target = document.createElement("div");
        const mounted = mount(view as FeatureView, { target });

        const frame = target.querySelector("iframe");
        target.querySelector("button")?.click();
        void unmount(mounted);

        expect([
            frame?.getAttribute("sandbox"),
            frame?.getAttribute("allow"),
            frame?.getAttribute("src"),
            frame?.getAttribute("title"),
        ]).toEqual([
            "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox",
            "",
            SHOWN.src,
            words["stage.frameTitle"]({ title: "Skat" }),
        ]);
        expect(frame?.getAttribute("referrerpolicy")).toBe("no-referrer");
        expect(page.sent).toEqual([{ type: STAGE_SIGNAL.STAGE_DETACH, attachment: 2 }]);
    });

    it("offers no removal to a seat that may not remove the table", async () => {
        const page = await installedStage();
        page.emit(SIGNAL_TYPE.ROOM_CREATED, { participant_id: "me", is_host: false });
        page.emit(STAGE_SIGNAL.STAGE_TABLE, { ...SHOWN, opener_id: "someone" });
        const [view] = page.stage;
        const target = document.createElement("div");

        const mounted = mount(view as FeatureView, { target });
        const offered = target.querySelector("button");
        void unmount(mounted);

        expect(offered).toBeNull();
    });

    it("is cleared when the call ends", async () => {
        const page = await installedStage();
        page.emit(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participant_id: "me", is_host: true });
        page.emit(STAGE_SIGNAL.STAGE_TABLE, SHOWN);

        page.endCall();

        expect(page.stage.map((view) => view !== null)).toEqual([true, false]);
        expect(page.item.hidden).toBe(false);
    });
});

describe("a refusal", () => {
    it("of a link is told with the title of the app whose field it was pasted into", async () => {
        const page = await installedStage();
        page.use("chess");
        page.paste("https://skat.example/t/a");

        page.emit(STAGE_SIGNAL.STAGE_REFUSED, { reason: STAGE_REFUSAL.BAD_LINK });

        expect(page.toasts).toEqual([words["stage.badLinkToast"]({ title: "Schach" })]);
    });

    it("of a second table is told with the title of the app on the stage", async () => {
        const page = await installedStage();
        page.emit(STAGE_SIGNAL.STAGE_TABLE, SHOWN);

        page.emit(STAGE_SIGNAL.STAGE_REFUSED, { reason: STAGE_REFUSAL.BUSY });

        expect(page.toasts).toEqual([words["stage.busyToast"]({ title: "Skat" })]);
    });

    it("of a second table names an app the page was not told of by its name", async () => {
        const page = await installedStage();
        page.emit(STAGE_SIGNAL.STAGE_TABLE, { ...SHOWN, app: "other" });

        page.emit(STAGE_SIGNAL.STAGE_REFUSED, { reason: STAGE_REFUSAL.BUSY });

        expect(page.toasts).toEqual([words["stage.busyToast"]({ title: "other" })]);
    });

    it("of a removal by a seat that may not is told", async () => {
        const page = await installedStage();

        page.emit(STAGE_SIGNAL.STAGE_REFUSED, { reason: STAGE_REFUSAL.NOT_OPENER_OR_HOST });

        expect(page.toasts).toEqual([words["stage.notRemovedToast"]]);
    });

    it("of a kind this page does not know is not toasted", async () => {
        const page = await installedStage();

        page.emit(STAGE_SIGNAL.STAGE_REFUSED, { reason: "something_new" });

        expect(page.toasts).toEqual([]);
    });
});

describe("the start link", () => {
    const START_LINK = { app: "skat", context: "a".repeat(32) };
    const CREATED = { participant_id: "me", is_host: true };

    it("attaches its table to the call the page starts, by app and key", async () => {
        const page = await installedStage(START_LINK);

        page.emit(SIGNAL_TYPE.ROOM_CREATED, CREATED);

        expect(page.sent).toEqual([{ type: STAGE_SIGNAL.STAGE_ATTACH, ...START_LINK }]);
    });

    it("attaches once, however often a room is created afterwards", async () => {
        const page = await installedStage(START_LINK);

        page.emit(SIGNAL_TYPE.ROOM_CREATED, CREATED);
        page.emit(SIGNAL_TYPE.ROOM_CREATED, CREATED);

        expect(page.sent).toHaveLength(1);
    });

    it("attaches nothing before a room is created", async () => {
        const page = await installedStage(START_LINK);

        page.emit(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participant_id: "me", is_host: false });

        expect(page.sent).toEqual([]);
    });

    it("attaches nothing at any other address", async () => {
        const page = await installedStage();

        page.emit(SIGNAL_TYPE.ROOM_CREATED, CREATED);

        expect(page.sent).toEqual([]);
    });
});
