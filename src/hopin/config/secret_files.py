from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from pathlib import Path


class StartRefusedError(Exception):
    """A setting cannot start HopIn; the message names settings, never their values."""


def read_secret_file(path: Path, setting: str) -> SecretStr:
    try:
        content = path.read_bytes().decode("utf-8").removesuffix("\n")
    except OSError, UnicodeDecodeError:
        raise StartRefusedError(f"{setting} names a file that cannot be read") from None
    if not content:
        raise StartRefusedError(f"{setting} names an empty file") from None
    return SecretStr(content)
