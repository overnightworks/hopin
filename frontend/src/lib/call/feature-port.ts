// The seam where a feature's browser module reaches the call core. The page hands `FeatureCore` to each
// feature's `install` before signaling connects; besides `featureView`, which builds what it hands over, a
// feature imports only the generated wires and the core's types.

import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import type { TOAST_KIND, ToastAction, ToastKind } from "#lib/ui/toast.svelte.ts";

export interface OutgoingFrame {
    type: string;
    [field: string]: unknown;
}

export interface FeatureCore {
    /** The handler declares the fields of the frames it registers for; the server it trusts sends them. */
    on(type: string, handler: (frame: never) => void): void;
    send(frame: OutgoingFrame): void;
    isConnected(): boolean;
    showToast(text: string, kind: ToastKind, action: ToastAction | null): void;
    toastKind: typeof TOAST_KIND;
    addMoreItems(...items: Element[]): void;
    /** Show a line no person wrote, counted as unread like a person's. */
    receiveChatLine(view: FeatureView): void;
    /** Keep a view below every later line until another view, or null, takes its place. */
    pinLastChatLine(view: FeatureView | null): void;
    /** Show a view between the chat's header and its lines until another view, or null, takes its place. */
    showChatCard(view: FeatureView | null): void;
    onCallEnded(hook: () => void): void;
}

export interface FeatureModule {
    install(core: FeatureCore): Promise<void>;
}
