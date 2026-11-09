import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "hopin"
CLIENT = "agent_runtime_client"
HTTP_LIBRARY = "httpx2"
BACKEND_MODULE = SOURCE / "agent" / "backend.py"
SETTINGS_MODULE = SOURCE / "agent" / "settings.py"
CLIENT_CONFIG = f"{CLIENT}.config"
# The tools name only the client's tool contract and the member records, and the MCP module only the MCP profile, the
# member records and the tool contract.
CLIENT_PARTS_OF = {
    SOURCE / "agent" / "tools.py": {f"{CLIENT}.member", f"{CLIENT}.tools"},
    SOURCE / "agent" / "mcp.py": {
        f"{CLIENT}.mcp_server",
        f"{CLIENT}.mcp_wire",
        f"{CLIENT}.member",
        f"{CLIENT}.tools",
    },
}
SPEECH_CLIENT = "speech_client"
SPEECH_MODULE = SOURCE / "agent" / "speech.py"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def names(module: str, library: str) -> bool:
    return module == library or module.startswith(f"{library}.")


@pytest.mark.parametrize("path", sorted(SOURCE.rglob("*.py")), ids=lambda path: str(path.relative_to(SOURCE)))
def test_only_the_connector_names_the_client_and_only_its_settings_name_the_client_config(path):
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


@pytest.mark.parametrize("path", sorted(SOURCE.rglob("*.py")), ids=lambda path: str(path.relative_to(SOURCE)))
def test_only_the_speech_connector_names_the_speech_client(path):
    named = {module for module in imported_modules(path) if names(module, SPEECH_CLIENT)}

    assert bool(named) == (path == SPEECH_MODULE)
