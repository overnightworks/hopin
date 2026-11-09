"""Splits the end-to-end suite across CI runners: each runner keeps every count-th collected test."""

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

SHARD_VARIABLE = "HOPIN_E2E_SHARD"
SHARDS_VARIABLE = "HOPIN_E2E_SHARDS"


def shard_from_environment(environment: Mapping[str, str]) -> tuple[int, int] | None:
    """The (index, count) the environment names, or None when it names neither."""
    index_text = environment.get(SHARD_VARIABLE)
    count_text = environment.get(SHARDS_VARIABLE)
    if index_text is None and count_text is None:
        return None
    if index_text is None or count_text is None:
        message = f"{SHARD_VARIABLE} and {SHARDS_VARIABLE} are set together or not at all"
        raise pytest.UsageError(message)
    try:
        index, count = int(index_text), int(count_text)
    except ValueError:
        message = f"{SHARD_VARIABLE} and {SHARDS_VARIABLE} must be integers, got {index_text!r} and {count_text!r}"
        raise pytest.UsageError(message) from None
    if count < 1:
        message = f"{SHARDS_VARIABLE} must be at least 1, got {count}"
        raise pytest.UsageError(message)
    if not 0 <= index < count:
        message = f"{SHARD_VARIABLE} must be from 0 to {count - 1}, got {index}"
        raise pytest.UsageError(message)
    return index, count


def select_shard(config: pytest.Config, items: list[pytest.Item], suite_directory: Path) -> None:
    """Keeps this shard's share of the suite's items in place; the rest are reported as deselected."""
    shard = shard_from_environment(os.environ)
    if shard is None:
        return
    index, count = shard
    suite_items = [item for item in items if item.path.is_relative_to(suite_directory)]
    rejected = {id(item) for position, item in enumerate(suite_items) if position % count != index}
    items[:] = [item for item in items if id(item) not in rejected]
    config.hook.pytest_deselected(items=[item for item in suite_items if id(item) in rejected])
