"""Search service application package."""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "1.0.0"


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Populate ``os.environ`` from a ``.env`` file at first import.

    Matches the behavior the README promises for the non-Docker workflow:
    ``SEARCH_ROUTER_API_KEY`` and friends placed in ``.env`` are picked up by
    ``uvicorn app.main:app``. Real environment variables always win, so this
    is a no-op when the process already has them exported.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv()
