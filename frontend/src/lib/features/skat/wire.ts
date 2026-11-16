// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum SKAT_SIGNAL {
    SKAT_ATTACH = "skat_attach",
    SKAT_DETACH = "skat_detach",
    SKAT_TABLE = "skat_table",
    SKAT_REFUSED = "skat_refused",
}

export enum SKAT_REFUSAL {
    BAD_LINK = "bad_link",
    BUSY = "busy",
    NOT_OPENER_OR_HOST = "not_opener_or_host",
}

export type AttachedTableFrame = {
    type: SKAT_SIGNAL.SKAT_TABLE;
    revision: number;
    attachment: number;
    table: string;
    src: string;
    opener_id: string | null;
};

export type EmptyTableFrame = {
    type: SKAT_SIGNAL.SKAT_TABLE;
    revision: number;
    attachment: null;
    table: null;
    src: null;
    opener_id: null;
};

export type RefusedFrame = {
    type: SKAT_SIGNAL.SKAT_REFUSED;
    reason: SKAT_REFUSAL;
};

export type SkatFrameByType = {
    [SKAT_SIGNAL.SKAT_TABLE]: AttachedTableFrame | EmptyTableFrame;
    [SKAT_SIGNAL.SKAT_REFUSED]: RefusedFrame;
};
