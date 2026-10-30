import { ownPicture } from "#lib/ui/stage.svelte.ts";

// Svelte runs `update` when the delivery changes, never at mount: the own videos mount before any stream arrives.
// The delivery is the action's argument only so that Svelte calls `update` when it changes.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function ownPictureMedia(video: HTMLVideoElement, _delivery: number) {
    return {
        update() {
            video.srcObject = ownPicture.stream;
        },
    };
}
