import { updated } from "$app/state";
import type { ClientInit } from "@sveltejs/kit/hooks";

// A chunk of the page this tab opened on is gone once a release replaced the build; the reload applies only then, so
// a chunk missing for any other reason cannot loop. The event is not cancelled: the failed import still reports itself.
export const init: ClientInit = () => {
    window.addEventListener("vite:preloadError", () => {
        void updated.check().then((newVersionIsOut) => {
            if (newVersionIsOut) location.reload();
        });
    });
};
