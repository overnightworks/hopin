import json
from pathlib import Path
from typing import Any

CLIENT_MANIFEST = (
    Path(__file__).parents[3] / "frontend" / ".svelte-kit" / "output" / "client" / ".vite" / "manifest.json"
)


def _manifest() -> dict[str, dict[str, Any]]:
    return json.loads(CLIENT_MANIFEST.read_text())


def entry_of(feature: str) -> dict[str, Any]:
    """The page build's manifest entry of a feature's own chunk; the build names its files by a content hash."""
    return _manifest()[f"src/lib/features/{feature}/index.ts"]


def urls_of_the_chunks_of(feature: str) -> set[str]:
    """Where the page build serves a feature's own chunk and every chunk that chunk imports on demand."""
    manifest = _manifest()
    entry = entry_of(feature)
    return {f"/{entry['file']}", *(f"/{manifest[key]['file']}" for key in entry.get("dynamicImports", []))}
