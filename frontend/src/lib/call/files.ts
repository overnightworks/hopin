// The one owner that moves a posted file between the browsers. The sender announces a file on the line channel (the
// lines exchange queues, paces and counts the announcement); each receiver then fetches its bytes on a data channel of
// its own for that file, in chunks, while the sender waits on the channel's send buffer. A small picture is fetched at
// once, any other file on a tap. The receiver enforces the caps: a file over the per-file cap is refused, a fetch
// while the same sender has another in flight is refused, and so is one that takes the bytes received in this call
// beyond the total cap; each refusal is shown. A picture is shown only when its first bytes pass the raster check; any
// other file is offered as a download in a blob typed as opaque bytes. A file whose sender leaves, or whose channel
// closes before its last byte, is discarded and shown incomplete. Every blob URL and held file is released with
// `release`, at leave and at the call's end. An announcement is answered with the lines exchange's receipt when the
// exchange is done with it, so the sender's file shows pending until every receiver is done with the announcement; the
// composer stays busy while any receiver fetches a file from it.

import {
    DOWNLOAD_TYPE,
    FILE_REFUSAL,
    fileRefusal,
    isFetchedAtOnce,
    PICTURE_HEAD_BYTES,
    pictureTypeOf,
    safeFileName,
} from "#lib/call/file.ts";
import { DELIVERY, type Delivery, type FileAnnouncement, lines, type Posted } from "#lib/call/lines.ts";
import { state } from "#lib/call/state.ts";
import { FILE_MAX_IN_FLIGHT, FILE_MAX_RECEIVED_BYTES } from "#lib/wire.ts";

export const FILE_STATE = {
    /** Announced and not fetched: it waits for a tap. */
    OFFERED: "offered",
    LOADING: "loading",
    DONE: "done",
    /** Discarded: the sender left or the channel closed before the file's end. */
    INCOMPLETE: "incomplete",
    /** Over the per-file cap: never fetched. */
    REFUSED: "refused",
} as const;
export type FileState = (typeof FILE_STATE)[keyof typeof FILE_STATE];

/** Why a fetch did not start; the file stays offered and a tap tries again. */
export const FILE_PROBLEM = {
    /** The same sender's other file is in flight. */
    BUSY: "busy",
    /** The bytes received in this call would pass the total cap. */
    TOTAL: "total",
} as const;
export type FileProblem = (typeof FILE_PROBLEM)[keyof typeof FILE_PROBLEM];

/** What the chat shows of a file: a snapshot, replaced by each change. */
export interface FileView {
    key: string;
    id: number;
    own: boolean;
    sender: string;
    name: string;
    size: number;
    state: FileState;
    problem: FileProblem | null;
    /** The blob URL of a picture or a download, none while there are no bytes to show or offer. */
    url: string | null;
    picture: boolean;
    /** The delivery of the sender's own file; a received file is delivered. */
    delivery: Delivery;
    missing: readonly string[];
}

/** The part of a data channel the exchange moves bytes over. */
export interface FileChannel {
    readonly label: string;
    readonly readyState: string;
    readonly bufferedAmount: number;
    bufferedAmountLowThreshold: number;
    binaryType: string;
    send(data: ArrayBuffer): void;
    close(): void;
    onopen: ((event: Event) => void) | null;
    onclose: ((event: Event) => void) | null;
    onmessage: ((event: MessageEvent) => void) | null;
    onbufferedamountlow: ((event: Event) => void) | null;
}

/** How the exchange opens a channel to a person for one file; the fetching side opens it and the sender serves it. */
export interface FileTransport {
    open(label: string): FileChannel;
}

export interface FileEvents {
    shown: (view: FileView) => void;
    changed: (view: FileView) => void;
}

/** Why a file is not posted. */
export const POST_REFUSAL = {
    EMPTY: FILE_REFUSAL.EMPTY,
    TOO_LARGE: FILE_REFUSAL.TOO_LARGE,
    /** The sender's previous file is still on its way. */
    BUSY: "busy",
} as const;
export type PostRefusal = (typeof POST_REFUSAL)[keyof typeof POST_REFUSAL];

/** The part of the lines exchange the files use. */
export interface Announcer {
    announce: (announcement: FileAnnouncement) => Posted;
    acknowledge: (participantId: string, id: number) => void;
}

export interface FilesEnvironment {
    announcer: Announcer;
    nameOf: (participantId: string) => string;
    createUrl: (blob: Blob) => string;
    revokeUrl: (url: string) => void;
}

const CHUNK_BYTES = 16 * 1024;
const SEND_BUFFER_HIGH_BYTES = 1024 * 1024;
const SEND_BUFFER_LOW_BYTES = 256 * 1024;
const CHANNEL_OPEN = "open";
const CHANNEL_CONNECTING = "connecting";
const LABEL_PATTERN = /^file:(\d+)$/;

function labelOf(id: number): string {
    return `file:${String(id)}`;
}

interface FileRecord {
    view: FileView;
    participantId: string | null;
    chunks: ArrayBuffer[];
    received: number;
    channel: FileChannel | null;
    acknowledged: boolean;
}

interface Held {
    file: Blob;
    /** The people the announcement went to: only they may fetch. */
    audience: Set<string>;
    served: Set<string>;
}

/** The first bytes of the chunks, as many as the raster check reads. */
function headOf(chunks: readonly ArrayBuffer[]): Uint8Array {
    const head = new Uint8Array(PICTURE_HEAD_BYTES);
    let length = 0;
    for (const chunk of chunks) {
        const bytes = new Uint8Array(chunk, 0, Math.min(chunk.byteLength, PICTURE_HEAD_BYTES - length));
        head.set(bytes, length);
        length += bytes.length;
        if (length === PICTURE_HEAD_BYTES) break;
    }
    return head.subarray(0, length);
}

export function createFiles({ announcer, nameOf, createUrl, revokeUrl }: FilesEnvironment) {
    const transports = new Map<string, FileTransport>();
    const records = new Map<string, FileRecord>();
    const held = new Map<number, Held>();
    /** The sender's channels open to each person that fetches from it. */
    const serving = new Map<string, Set<FileChannel>>();
    /** The files each sender has in flight at this page. */
    const fetching = new Map<string, Set<FileRecord>>();
    let reserved = 0;
    /** Counts the releases: work that was under way when the call ended finds the count changed and leaves. */
    let generation = 0;
    let events: FileEvents = { shown: () => {}, changed: () => {} };

    function emit(record: FileRecord, change: Partial<FileView>) {
        Object.assign(record.view, change);
        events.changed({ ...record.view });
    }

    function acknowledge(record: FileRecord) {
        if (record.acknowledged || record.participantId === null) return;
        record.acknowledged = true;
        announcer.acknowledge(record.participantId, record.view.id);
    }

    function closeChannel(record: FileRecord) {
        const channel = record.channel;
        record.channel = null;
        if (channel === null) return;
        channel.onmessage = channel.onclose = null;
        channel.close();
    }

    function inFlightFrom(participantId: string): number {
        return fetching.get(participantId)?.size ?? 0;
    }

    function leaveFetching(record: FileRecord) {
        fetching.get(record.participantId as string)?.delete(record);
    }

    function discard(record: FileRecord) {
        if (record.view.state === FILE_STATE.LOADING) reserved -= record.view.size - record.received;
        record.chunks = [];
        leaveFetching(record);
        closeChannel(record);
        emit(record, { state: FILE_STATE.INCOMPLETE, problem: null });
        acknowledge(record);
    }

    function finish(record: FileRecord) {
        const pictureType = pictureTypeOf(headOf(record.chunks));
        const blob = new Blob(record.chunks, { type: pictureType ?? DOWNLOAD_TYPE });
        record.chunks = [];
        leaveFetching(record);
        closeChannel(record);
        emit(record, { state: FILE_STATE.DONE, url: createUrl(blob), picture: pictureType !== null });
        acknowledge(record);
    }

    function receiveChunk(record: FileRecord, data: unknown) {
        // A sender sends no empty chunk: empty ones would be kept without bringing the file nearer its end.
        if (
            !(data instanceof ArrayBuffer) ||
            data.byteLength === 0 ||
            record.received + data.byteLength > record.view.size
        ) {
            discard(record);
            return;
        }
        record.chunks.push(data);
        record.received += data.byteLength;
        if (record.received === record.view.size) finish(record);
    }

    function refuseFetch(record: FileRecord, problem: FileProblem) {
        emit(record, { problem });
        acknowledge(record);
    }

    function fetchFile(record: FileRecord) {
        const { participantId, view } = record;
        if (participantId === null || view.state !== FILE_STATE.OFFERED) return;
        const transport = transports.get(participantId) as FileTransport;
        if (inFlightFrom(participantId) >= FILE_MAX_IN_FLIGHT) {
            refuseFetch(record, FILE_PROBLEM.BUSY);
            return;
        }
        if (reserved + view.size > FILE_MAX_RECEIVED_BYTES) {
            refuseFetch(record, FILE_PROBLEM.TOTAL);
            return;
        }
        reserved += view.size;
        fetching.set(participantId, (fetching.get(participantId) ?? new Set()).add(record));
        const channel = transport.open(labelOf(view.id));
        channel.binaryType = "arraybuffer";
        channel.onmessage = (event) => {
            receiveChunk(record, event.data);
        };
        channel.onclose = () => {
            discard(record);
        };
        record.channel = channel;
        emit(record, { state: FILE_STATE.LOADING, problem: null });
    }

    function show(record: FileRecord) {
        records.set(record.view.key, record);
        events.shown({ ...record.view });
    }

    async function sendChunks(file: Blob, channel: FileChannel) {
        let wake: (() => void) | null = null;
        const wait = () =>
            new Promise<void>((resolve) => {
                wake = resolve;
            });
        channel.onopen =
            channel.onbufferedamountlow =
            channel.onclose =
                () => {
                    wake?.();
                };
        const waitWhile = async (blocked: () => boolean): Promise<void> => {
            if (!blocked()) return;
            await wait();
            await waitWhile(blocked);
        };
        channel.bufferedAmountLowThreshold = SEND_BUFFER_LOW_BYTES;
        await waitWhile(() => channel.readyState === CHANNEL_CONNECTING);
        for (let offset = 0; offset < file.size; offset += CHUNK_BYTES) {
            await waitWhile(
                () => channel.readyState === CHANNEL_OPEN && channel.bufferedAmount > SEND_BUFFER_HIGH_BYTES,
            );
            const chunk = await file.slice(offset, offset + CHUNK_BYTES).arrayBuffer();
            if (channel.readyState !== CHANNEL_OPEN) return;
            channel.send(chunk);
        }
        // The receiver closes the channel once it holds the last byte: a close from this side could cut data still on its way.
        await waitWhile(() => channel.readyState === CHANNEL_OPEN);
    }

    return {
        /** Take the events the exchange reports; the chat is the one listener. */
        listen(listener: FileEvents) {
            events = listener;
        },

        /** The way to open a channel to the person, made with its peer connection. */
        addPeer(participantId: string, transport: FileTransport) {
            transports.set(participantId, transport);
        },

        /** The person left the call: what it still sends is discarded and shown incomplete, what it offered is gone, and what is sent to it stops. */
        removePeer(participantId: string) {
            transports.delete(participantId);
            for (const record of records.values()) {
                const { state: current } = record.view;
                const unfinished = current === FILE_STATE.OFFERED || current === FILE_STATE.LOADING;
                if (record.participantId === participantId && unfinished) discard(record);
            }
            for (const channel of serving.get(participantId) ?? []) channel.close();
            serving.delete(participantId);
            for (const file of held.values()) file.audience.delete(participantId);
        },

        /** A file announced by the person: shown at once, and fetched at once when it is a small picture. */
        announced(participantId: string, id: number, announcement: FileAnnouncement) {
            const key = `${participantId}:${String(id)}`;
            const refusal = fileRefusal(announcement.size);
            if (records.has(key) || refusal === FILE_REFUSAL.EMPTY) return;
            const tooLarge = refusal === FILE_REFUSAL.TOO_LARGE;
            const record: FileRecord = {
                view: {
                    key,
                    id,
                    own: false,
                    sender: nameOf(participantId),
                    name: safeFileName(announcement.name),
                    size: announcement.size,
                    state: tooLarge ? FILE_STATE.REFUSED : FILE_STATE.OFFERED,
                    problem: null,
                    url: null,
                    picture: false,
                    delivery: DELIVERY.DELIVERED,
                    missing: [],
                },
                participantId,
                chunks: [],
                received: 0,
                channel: null,
                acknowledged: false,
            };
            show(record);
            if (!tooLarge && isFetchedAtOnce(announcement.mime, announcement.size)) {
                fetchFile(record);
                return;
            }
            acknowledge(record);
        },

        /** The person taps a file that waits: fetch it now. */
        fetch(key: string) {
            const record = records.get(key);
            if (record !== undefined) fetchFile(record);
        },

        /** Post a file to everyone the page shows; the reason it is not posted otherwise. */
        post(file: File): PostRefusal | null {
            const refusal = fileRefusal(file.size);
            if (refusal !== null) return refusal;
            const announcedInFlight = [...records.values()].some(
                (record) => record.view.own && record.view.delivery === DELIVERY.PENDING,
            );
            const served = [...serving.values()].some((channels) => channels.size > 0);
            if (announcedInFlight || served) return POST_REFUSAL.BUSY;
            const name = safeFileName(file.name);
            const postedIn = generation;
            const posted = announcer.announce({ name, size: file.size, mime: file.type });
            held.set(posted.id, { file, audience: new Set(transports.keys()), served: new Set() });
            const record: FileRecord = {
                view: {
                    key: `own:${String(posted.id)}`,
                    id: posted.id,
                    own: true,
                    sender: "",
                    name,
                    size: file.size,
                    state: FILE_STATE.DONE,
                    problem: null,
                    url: null,
                    picture: false,
                    delivery: posted.delivery,
                    missing: posted.missing,
                },
                participantId: null,
                chunks: [],
                received: 0,
                channel: null,
                acknowledged: true,
            };
            show(record);
            void file
                .slice(0, PICTURE_HEAD_BYTES)
                .arrayBuffer()
                .then((head) => {
                    const pictureType = pictureTypeOf(new Uint8Array(head));
                    if (generation !== postedIn || pictureType === null) return;
                    emit(record, { picture: true, url: createUrl(file.slice(0, file.size, pictureType)) });
                });
            return null;
        },

        /** The delivery of the sender's own file `id` changed. */
        delivered(id: number, delivery: Delivery, missing: readonly string[]) {
            const record = records.get(`own:${String(id)}`);
            if (record !== undefined) emit(record, { delivery, missing });
        },

        /** The channel a person opened to fetch a file: it is served when it names a file the person was told of and no other is sent to it. */
        serve(participantId: string, channel: FileChannel) {
            const match = LABEL_PATTERN.exec(channel.label);
            const file = match === null ? undefined : held.get(Number(match[1]));
            const unknown = file === undefined || !file.audience.has(participantId) || file.served.has(participantId);
            const channels = serving.get(participantId) ?? new Set<FileChannel>();
            if (unknown || channels.size >= FILE_MAX_IN_FLIGHT) {
                channel.close();
                return;
            }
            file.served.add(participantId);
            serving.set(participantId, channels.add(channel));
            void sendChunks(file.file, channel).finally(() => {
                channels.delete(channel);
            });
        },

        /** Free every blob URL and held file, close every channel and begin the next call with an empty count: at leave and at the call's end. */
        release() {
            generation += 1;
            for (const channels of serving.values()) for (const channel of channels) channel.close();
            serving.clear();
            held.clear();
            fetching.clear();
            for (const record of records.values()) {
                closeChannel(record);
                if (record.view.url !== null) revokeUrl(record.view.url);
                emit(record, { state: FILE_STATE.INCOMPLETE, problem: null, url: null });
            }
            records.clear();
            transports.clear();
            reserved = 0;
        },
    };
}

export const files = createFiles({
    announcer: lines,
    nameOf: (participantId) => state.namesByParticipant.get(participantId) ?? "",
    createUrl: (blob) => URL.createObjectURL(blob),
    revokeUrl: (url) => {
        URL.revokeObjectURL(url);
    },
});
