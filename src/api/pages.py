"""Cursor pages (API plan section 5).

    GET /v1/requisitions?limit=50&cursor=<next_cursor>
    {"items": [...], "next_cursor": "..." | null}

Lists are newest first ("before" cursors); the event feed is oldest first ("after" cursors). A
cursor is opaque: pass next_cursor back unchanged. null means the last page. A list query fetches
limit + 1 rows, so one extra row is how another page is known to exist.
"""

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from api.errors import ApiError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_LARGEST_ID = 10**18

Direction = Literal["before", "after"]


def encode_cursor(position: int, direction: Direction = "before") -> str:
    raw = json.dumps({direction: position}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, direction: Direction = "before") -> int | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        position = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))[direction]
        is_number = isinstance(position, int) and not isinstance(position, bool)
        if is_number and 0 < position < _LARGEST_ID:
            return int(position)
    except (ValueError, TypeError, KeyError, binascii.Error):
        pass
    raise ApiError(
        400,
        "invalid_cursor",
        "The cursor is not valid. Pass next_cursor from the previous page unchanged.",
    )


def next_cursor(
    rows: Sequence[Mapping[str, Any]], limit: int, direction: Direction = "before"
) -> str | None:
    """Rows were fetched with limit + 1: an extra row means there is another page."""
    if len(rows) <= limit:
        return None
    return encode_cursor(int(rows[limit - 1]["id"]), direction)
