"""Cursor pages (API plan section 5).

    GET /v1/requisitions?limit=50&cursor=<next_cursor>
    {"items": [...], "next_cursor": "..." | null}

Lists are newest first. A cursor is opaque: pass next_cursor back unchanged. null means the last
page. A list query fetches limit + 1 rows, so one extra row is how another page is known to exist.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any

from api.errors import ApiError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_LARGEST_ID = 10**18


def encode_cursor(before_id: int) -> str:
    raw = json.dumps({"before": before_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        before = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))["before"]
        if isinstance(before, int) and not isinstance(before, bool) and 0 < before < _LARGEST_ID:
            return before
    except (ValueError, TypeError, KeyError, binascii.Error):
        pass
    raise ApiError(
        400,
        "invalid_cursor",
        "The cursor is not valid. Pass next_cursor from the previous page unchanged.",
    )


def next_cursor(rows: Sequence[Mapping[str, Any]], limit: int) -> str | None:
    """Rows were fetched with limit + 1: an extra row means there is another page."""
    return encode_cursor(int(rows[limit - 1]["id"])) if len(rows) > limit else None
