<script lang="ts">
    import { SignInPage, type KitWords, type SignInState } from "overnightworks-design";
    import { LEGAL_LINKS } from "#lib/legal.ts";
    import { AUTH_START_PATH, LOGOUT_PATH } from "#lib/wire.ts";

    const { page, words }: { page: Exclude<SignInState["page"], "signing-in" | "window-blocked">; words: KitWords } =
        $props();
    const signInState = $derived.by((): SignInState => {
        if (page === "no-access") {
            return { page, logOutAction: LOGOUT_PATH };
        }
        return { page, signInHref: AUTH_START_PATH };
    });
</script>

<div class="sign-in">
    <SignInPage appName="HopIn" state={signInState} legalLinks={LEGAL_LINKS} {words} />
</div>
