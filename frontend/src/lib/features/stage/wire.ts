// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum STAGE_SIGNAL {
    STAGE_ATTACH = "stage_attach",
    STAGE_DETACH = "stage_detach",
    STAGE_TABLE = "stage_table",
    STAGE_REFUSED = "stage_refused",
}

export enum STAGE_REFUSAL {
    BAD_LINK = "bad_link",
    BUSY = "busy",
    NOT_OPENER_OR_HOST = "not_opener_or_host",
}

export type AttachedTableFrame = {
    type: STAGE_SIGNAL.STAGE_TABLE;
    revision: number;
    attachment: number;
    table: string;
    src: string;
    opener_id: string | null;
};

export type EmptyTableFrame = {
    type: STAGE_SIGNAL.STAGE_TABLE;
    revision: number;
    attachment: null;
    table: null;
    src: null;
    opener_id: null;
};

export type RefusedFrame = {
    type: STAGE_SIGNAL.STAGE_REFUSED;
    reason: STAGE_REFUSAL;
};

export type StageFrameByType = {
    [STAGE_SIGNAL.STAGE_TABLE]: AttachedTableFrame | EmptyTableFrame;
    [STAGE_SIGNAL.STAGE_REFUSED]: RefusedFrame;
};
