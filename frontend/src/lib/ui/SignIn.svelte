<script lang="ts">
    import { SignInPage, type SignInState } from "overnightworks-appkit";
    import { LEGAL_LINKS } from "#lib/legal.ts";
    import { AUTH_START_PATH, LOGOUT_PATH } from "#lib/wire.ts";

    const { page }: { page: SignInState["page"] } = $props();
    const signInState = $derived.by((): SignInState => {
        if (page === "no-access") {
            return { page, logOutAction: LOGOUT_PATH };
        }
        return { page, signInHref: AUTH_START_PATH };
    });
</script>

<div class="sign-in">
    <SignInPage appName="HopIn" state={signInState} legalLinks={LEGAL_LINKS} />
</div>
