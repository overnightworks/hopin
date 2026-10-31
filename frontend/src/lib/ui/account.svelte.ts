import { goThroughSignIn } from "#lib/ui/navigation.ts";
import { ACCOUNT_PATH } from "#lib/wire.ts";

const SIGNED_OUT_STATUS = 401;
const UNNAMED_MENU_NAME = "Account";
const INITIAL_SEPARATOR = /[._-]/;
const INITIAL_CHARACTER = /[\p{L}\p{N}]/u;
const INITIAL_COUNT = 2;

export const account: { name: string | null } = $state({ name: null });

export function menuName(): string {
    return account.name ?? UNNAMED_MENU_NAME;
}

export function avatarInitials(): string {
    if (account.name === null) return "";
    return account.name
        .split(INITIAL_SEPARATOR)
        .filter(Boolean)
        .slice(0, INITIAL_COUNT)
        .map((part) => part.match(INITIAL_CHARACTER)?.[0].toUpperCase() ?? "")
        .join("");
}

export async function readAccount(): Promise<string | null> {
    try {
        const answer = await fetch(ACCOUNT_PATH);
        if (answer.status === SIGNED_OUT_STATUS) {
            goThroughSignIn();
            return null;
        }
        if (!answer.ok) return null;
        const { name } = (await answer.json()) as { name?: unknown };
        if (typeof name !== "string") return null;
        account.name = name;
        return name;
    } catch {
        // The avatars keep the person icon when the name cannot be read.
        return null;
    }
}
