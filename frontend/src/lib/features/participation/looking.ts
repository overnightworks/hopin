import type { OutgoingFrame, PictureBounds, SendOptions } from "#lib/call/feature-port.ts";
import { LOOK_MAX_BYTES, LOOK_MAX_PIXELS, LOOK_PART_CHARACTERS, PARTICIPATION_SIGNAL } from "./wire.ts";

export const PICTURE_BOUNDS: PictureBounds = { maxPixels: LOOK_MAX_PIXELS, maxBytes: LOOK_MAX_BYTES };

// Requests answered or being answered, so a replayed request is not answered twice; a request id is 128 random bits.
const REMEMBERED_REQUESTS = 32;

export interface Looks {
    /** Answers the server's request: the picture in parts of at most LOOK_PART_CHARACTERS each, or the word that there is none when the camera gives no picture. */
    answer(requestId: string): Promise<void>;
}

/** `takePicture` takes one picture of the person's own camera within the bounds, and `send` hands a frame to the server; the parts of the picture are sent transient, so nothing keeps them. A request already answered is ignored. A camera that cannot be read answers as none, so the agent is not left waiting. */
export function createLooks(
    takePicture: (bounds: PictureBounds) => Promise<string | null>,
    send: (frame: OutgoingFrame, options?: SendOptions) => void,
): Looks {
    const answered = new Set<string>();

    function none(requestId: string) {
        send({ type: PARTICIPATION_SIGNAL.AGENT_LOOK_NONE, request_id: requestId });
    }

    return {
        async answer(requestId) {
            if (answered.has(requestId)) return;
            answered.add(requestId);
            if (answered.size > REMEMBERED_REQUESTS) answered.delete(answered.values().next().value as string);
            const picture = await takePicture(PICTURE_BOUNDS).catch(() => null);
            if (picture === null || picture === "") {
                none(requestId);
                return;
            }
            const count = Math.ceil(picture.length / LOOK_PART_CHARACTERS);
            for (let index = 0; index < count; index += 1) {
                send(
                    {
                        type: PARTICIPATION_SIGNAL.AGENT_LOOK_PART,
                        request_id: requestId,
                        index,
                        count,
                        data: picture.slice(index * LOOK_PART_CHARACTERS, (index + 1) * LOOK_PART_CHARACTERS),
                    },
                    { transient: true },
                );
            }
        },
    };
}
