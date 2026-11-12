"""The modules a source file imports, for tests that hold which module names which library."""

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


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
