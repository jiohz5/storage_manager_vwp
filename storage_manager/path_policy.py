from __future__ import annotations

from pathlib import Path
from typing import Union


PathLike = Union[str, Path]
SNAPSHOT_ROOT_NAME = ".snapshot"


def is_excluded_relative_path(relative_path: str) -> bool:
    normalized = str(relative_path).lstrip("/")
    return normalized == SNAPSHOT_ROOT_NAME or normalized.startswith(
        f"{SNAPSHOT_ROOT_NAME}/"
    )


def is_excluded_account_path(
    account_root: PathLike,
    candidate: PathLike,
) -> bool:
    root = Path(account_root).expanduser().absolute()
    path = Path(candidate).expanduser().absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return bool(relative.parts and relative.parts[0] == SNAPSHOT_ROOT_NAME)
