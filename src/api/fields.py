"""Field types shared by API responses and events."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def utc(value: datetime) -> str:
    """ISO-8601 in UTC with a Z suffix (API plan section 5)."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


Timestamp = Annotated[datetime, PlainSerializer(utc, return_type=str, when_used="json")]
