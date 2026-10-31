// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const { goThroughSignIn } = vi.hoisted(() => ({ goThroughSignIn: vi.fn() }));
vi.mock("#lib/ui/navigation.ts", () => ({ goThroughSignIn }));

// The module holds the read name, so each case loads a fresh copy.
async function freshAccount() {
    vi.resetModules();
    return import("#lib/ui/account.svelte.ts");
}

function answerWith(status: number, body: string) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status })));
}

afterEach(() => {
    vi.unstubAllGlobals();
    goThroughSignIn.mockReset();
});

describe("account name", () => {
    it("reads Account and no initials before any answer", async () => {
        const { avatarInitials, menuName } = await freshAccount();

        expect([menuName(), avatarInitials()]).toEqual(["Account", ""]);
    });

    it("resolves to the answered name and the menu follows it", async () => {
        answerWith(200, JSON.stringify({ name: "felix.hummert" }));
        const { menuName, readAccount } = await freshAccount();

        const name = await readAccount();

        expect([name, menuName()]).toEqual(["felix.hummert", "felix.hummert"]);
    });

    it.each([
        ["felix.hummert", "FH"],
        ["felix", "F"],
        ["a-b_c", "AB"],
        ["_.felix.hummert", "FH"],
        ["@felix.hummert", "FH"],
        ["@.felix", "F"],
        ["._-", ""],
        ["<b>x</b>", "B"],
    ])("takes the initials of %s as %j", async (name, initials) => {
        answerWith(200, JSON.stringify({ name }));
        const { avatarInitials, readAccount } = await freshAccount();

        await readAccount();

        expect(avatarInitials()).toBe(initials);
    });

    it("goes through the sign-in once on a 401 and reads no name", async () => {
        answerWith(401, "");
        const { menuName, readAccount } = await freshAccount();

        const name = await readAccount();

        expect([name, menuName(), goThroughSignIn.mock.calls.length]).toEqual([null, "Account", 1]);
    });

    it("reads no name on a 500 and does not go through the sign-in", async () => {
        answerWith(500, "");
        const { readAccount } = await freshAccount();

        const name = await readAccount();

        expect([name, goThroughSignIn.mock.calls.length]).toEqual([null, 0]);
    });

    it("reads no name when the request fails", async () => {
        vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
        const { menuName, readAccount } = await freshAccount();

        const name = await readAccount();

        expect([name, menuName()]).toEqual([null, "Account"]);
    });

    it("reads no name when the answer has no string name", async () => {
        answerWith(200, JSON.stringify({ name: 7 }));
        const { menuName, readAccount } = await freshAccount();

        const name = await readAccount();

        expect([name, menuName()]).toEqual([null, "Account"]);
    });

    it("reads no name when the answer does not parse", async () => {
        answerWith(200, "not json");
        const { readAccount } = await freshAccount();

        expect(await readAccount()).toBeNull();
    });
});
