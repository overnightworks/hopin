<script lang="ts">
    import { SignInPage } from "overnightworks-appkit";
    import { AUTH_START_PATH } from "#lib/wire.ts";

    const { page }: { page: "signed-out" | "login-failed" } = $props();
</script>

<div class="sign-in">
    <SignInPage
        appName="HopIn"
        state={{ page, signInHref: AUTH_START_PATH }}
        legalLinks={[
            { label: "Impressum", href: "https://hallucinai.de/impressum" },
            { label: "Datenschutz", href: "https://hallucinai.de/datenschutz#hopin" },
        ]}
    />
</div>
