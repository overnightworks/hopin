import { closeOpenMenu } from "#lib/ui/menus.ts";

export const SCREEN = {
    HOME: "home",
    PREJOIN: "prejoin",
    CALL: "call",
    END: "end",
} as const;

export type Screen = (typeof SCREEN)[keyof typeof SCREEN];

export const screens: { current: Screen | null } = $state({ current: null });

export function showScreen(screen: Screen) {
    closeOpenMenu();
    screens.current = screen;
}
