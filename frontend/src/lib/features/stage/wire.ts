// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.

export enum STAGE_SIGNAL {
    STAGE_APPS = "stage_apps",
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

export interface AppEntry {
    name: string;
    title: string;
}

export type AppsFrame = {
    type: STAGE_SIGNAL.STAGE_APPS;
    apps: AppEntry[];
};

export type AttachedTableFrame = {
    type: STAGE_SIGNAL.STAGE_TABLE;
    revision: number;
    attachment: number;
    app: string;
    table: string;
    src: string;
    opener_id: string | null;
};

export type EmptyTableFrame = {
    type: STAGE_SIGNAL.STAGE_TABLE;
    revision: number;
    attachment: null;
    app: null;
    table: null;
    src: null;
    opener_id: null;
};

export type RefusedFrame = {
    type: STAGE_SIGNAL.STAGE_REFUSED;
    reason: STAGE_REFUSAL;
};

export type StageFrameByType = {
    [STAGE_SIGNAL.STAGE_APPS]: AppsFrame;
    [STAGE_SIGNAL.STAGE_TABLE]: AttachedTableFrame | EmptyTableFrame;
    [STAGE_SIGNAL.STAGE_REFUSED]: RefusedFrame;
};
