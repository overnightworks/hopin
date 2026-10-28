import json
import re


def served_exports(module_text):
    """The wire module's exports by name, each value read back from its JSON."""
    return {
        name: json.loads(value)
        for name, value in re.findall(r"^export const (\w+) = (.*);$", module_text, re.MULTILINE)
    }
