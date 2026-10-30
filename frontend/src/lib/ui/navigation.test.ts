import { afterEach, describe, expect, it, vi } from "vitest";
import { goto } from "$app/navigation";
import { callUrl, getRoomIdFromUrl, goHome, goThroughSignIn, openCall, showCallAddress } from "#lib/ui/navigation.ts";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

function stubLocation(pathname: string) {
    const location = { pathname, origin: "https://hopin.test", reload: vi.fn(), assign: vi.fn() };
    vi.stubGlobal("location", location);
    return location;
}

afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
});

describe("navigation", () => {
    it.each([
        ["/c/abc", "abc"],
        ["/", null],
        ["/c", null],
        ["/c/", null],
        ["/x/abc", null],
    ])("reads the room id of %s as %s", (pathname, roomId) => {
        stubLocation(pathname);

        expect(getRoomIdFromUrl()).toBe(roomId);
    });

    it("builds the call link from the page origin", () => {
        stubLocation("/");

        expect(callUrl("abc")).toBe("https://hopin.test/c/abc");
    });

    it("rewrites the address with a shallow replace", async () => {
        await showCallAddress("abc");

        expect(goto).toHaveBeenCalledWith("/c/abc", { shallow: true, replace: true });
    });

    it("goes home with a document load", () => {
        const location = stubLocation("/c/abc");

        goHome();

        expect(location.assign).toHaveBeenCalledWith("/");
    });

    it("opens a call with a document load", () => {
        const location = stubLocation("/");

        openCall("abc");

        expect(location.assign).toHaveBeenCalledWith("https://hopin.test/c/abc");
    });

    it("goes through sign-in by reloading the document", () => {
        const location = stubLocation("/");

        goThroughSignIn();

        expect(location.reload).toHaveBeenCalled();
    });
});
