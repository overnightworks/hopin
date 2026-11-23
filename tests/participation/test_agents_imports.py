from pathlib import Path

import pytest

from tests.support.imported_modules import imported_modules, names

PARTICIPATION = Path(__file__).resolve().parents[2] / "src" / "hopin" / "participation"
CLIENT = "agents"
# Participation holds who is in a call and names the client only for its calling standard and its member records;
# import-linter squashes an external package to its root, so it cannot say this.
CLIENT_PARTS = (f"{CLIENT}.calling", f"{CLIENT}.member")


@pytest.mark.parametrize(
    "path", sorted(PARTICIPATION.rglob("*.py")), ids=lambda path: str(path.relative_to(PARTICIPATION))
)
def test_participation_names_only_the_calling_standard_and_the_member_records_of_the_client(path):
    named = {module for module in imported_modules(path) if names(module, CLIENT)}

    assert {module for module in named if not any(names(module, part) for part in CLIENT_PARTS)} == set()
