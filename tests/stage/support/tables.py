from hopin.stage.settings import StageApp

ORIGIN = "https://skat.example"
SKAT = StageApp(name="skat", title="Skat", origin=ORIGIN, frame_path="/t/{context}")
TABLE_ID = "0123456789abcdef0123456789abcdef"
TABLE_LINK = f"{ORIGIN}/t/{TABLE_ID}"
OTHER_TABLE_LINK = f"{ORIGIN}/t/{TABLE_ID[::-1]}"


def entry(**changes):
    """An entry of `HOPIN_STAGE_APPS` as the environment writes it, with the given fields changed."""
    return {"name": SKAT.name, "title": SKAT.title, "origin": SKAT.origin, "frame_path": SKAT.frame_path} | changes
