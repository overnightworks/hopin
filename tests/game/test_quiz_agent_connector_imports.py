from pathlib import Path

import pytest

from tests.support.imported_modules import imported_modules, names

SOURCE = Path(__file__).resolve().parents[2] / "src" / "hopin"
CLIENT = "agents"
HTTP_LIBRARY = "httpx2"
BACKEND_MODULE = SOURCE / "agent" / "backend.py"
SETTINGS_MODULE = SOURCE / "agent" / "settings.py"
CLIENT_CONFIG = f"{CLIENT}.config"
# The tools name only the client's tool contract and the member records, the MCP module only the MCP profile, the
# member records and the tool contract, the hearing adapter only the hearing record, the member records and the tool
# contract; import-linter squashes an external package to its root, so only this test can hold these parts.
# Participation is held by its import contract.
CLIENT_PARTS_OF = {
    SOURCE / "agent" / "tools.py": {f"{CLIENT}.member", f"{CLIENT}.tools"},
    SOURCE / "agent" / "mcp.py": {
        f"{CLIENT}.mcp_server",
        f"{CLIENT}.mcp_wire",
        f"{CLIENT}.member",
        f"{CLIENT}.tools",
    },
    SOURCE / "agent" / "hearing.py": {f"{CLIENT}.mcp_server", f"{CLIENT}.member", f"{CLIENT}.tools"},
}
PARTICIPATION = SOURCE / "participation"


@pytest.mark.parametrize(
    "path",
    sorted(path for path in SOURCE.rglob("*.py") if PARTICIPATION not in path.parents),
    ids=lambda path: str(path.relative_to(SOURCE)),
)
def test_each_module_names_only_its_allowed_parts_of_the_client_and_only_the_settings_name_its_config(path):
    modules = imported_modules(path)
    client = {module for module in modules if names(module, CLIENT)}
    client_config = {module for module in client if names(module, CLIENT_CONFIG)}

    assert not any(names(module, HTTP_LIBRARY) for module in modules)
    if path == BACKEND_MODULE:
        assert client_config == set()
    elif path == SETTINGS_MODULE:
        assert client == client_config
    elif path in CLIENT_PARTS_OF:
        assert client_config == set()
        assert all(any(names(module, part) for part in CLIENT_PARTS_OF[path]) for module in client)
    else:
        assert client == set()
