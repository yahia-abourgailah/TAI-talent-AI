"""Typed, opaque ids in the API (API plan section 5): req_…, app_…, cand_…, trn_…

The database keeps plain numbers. Callers must not parse an id or assume its length, so how an id
looks may change without a new API version. A malformed id is reported exactly like one that does
not exist or is out of the caller's scope.
"""

import re
from typing import Literal

from api.errors import ApiError
from pipeline.access import NotFound

Kind = Literal[
    "requisition", "application", "candidate", "transition", "review_item", "evaluation", "event"
]

PREFIX: dict[str, str] = {
    "requisition": "req",
    "application": "app",
    "candidate": "cand",
    "transition": "trn",
    "review_item": "rvw",
    "evaluation": "evl",
    "event": "evt",
}

NOT_FOUND: dict[str, str] = {
    "requisition": "Requisition not found.",
    "application": "Application not found.",
    "candidate": "Candidate not found.",
    "transition": "Transition not found.",
    "review_item": "Review item not found.",
    "evaluation": "Evaluation not found.",
    "event": "Event not found.",
}


def encode(kind: Kind, value: int) -> str:
    return f"{PREFIX[kind]}_{value}"


def decode(kind: Kind, value: str) -> int:
    """The number behind an id in a path or body. Anything malformed is simply not found."""
    match = re.fullmatch(rf"{PREFIX[kind]}_([1-9][0-9]{{0,17}})", value)
    if match is None:
        raise NotFound(NOT_FOUND[kind])
    return int(match.group(1))


def decode_filter(kind: Kind, name: str, value: str | None) -> int | None:
    """An id used as a list filter. A malformed one is a bad request, not an empty page."""
    if value is None:
        return None
    try:
        return decode(kind, value)
    except NotFound:
        label = kind.replace("_", " ")
        raise ApiError(400, "invalid_request", f"{name} is not a {label} id.") from None
