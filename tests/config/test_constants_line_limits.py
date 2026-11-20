import json
import unicodedata
from pathlib import Path

import pytest

from hopin.config.constants import LINE_LIMITS
from hopin.participation.text import clean_line

CASES = json.loads((Path(__file__).parent / "fixtures" / "line_cases.json").read_text(encoding="utf-8"))
BYTE_ORDER_MARK = "﻿"


def _expanded(case: dict[str, object]) -> tuple[str, str | None]:
    repeat = case.get("repeat", 1)
    text = str(case["text"]) * int(repeat)  # type: ignore[call-overload]
    expected = case["expected"]
    return text, None if expected is None else str(expected) * int(repeat)  # type: ignore[call-overload]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_a_text_is_judged_as_the_case_file_says(case):
    text, expected = _expanded(case)

    assert clean_line(text) == expected


def test_the_refused_characters_are_the_control_characters():
    every_character = [chr(code_point) for code_point in range(0x110000)]

    controls = {character for character in every_character if unicodedata.category(character) == "Cc"}

    assert LINE_LIMITS.refused == controls


def test_the_trimmed_characters_are_the_whitespace_python_trims_and_the_byte_order_mark():
    every_character = [chr(code_point) for code_point in range(0x110000)]

    whitespace = {character for character in every_character if character.isspace()}

    assert LINE_LIMITS.trimmed == whitespace | {BYTE_ORDER_MARK}
