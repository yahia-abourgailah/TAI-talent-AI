"""Where raw captures and the workbook files are kept (BR-107). Keys carry a content hash, so the
same bytes are stored once and an object is never overwritten."""

import base64
import hashlib
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import Settings, get_settings

_MISSING = {"404", "NoSuchKey", "NotFound"}


class BlobStore(Protocol):
    def put_if_absent(self, key: str, body: bytes, media_type: str) -> bool:
        """Stores the bytes unless the key exists. True when it wrote."""
        ...

    def get(self, key: str) -> bytes | None:
        """The stored bytes, or None when the key does not exist."""
        ...


class S3BlobStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_if_absent(self, key: str, body: bytes, media_type: str) -> bool:
        # Keys are content hashes, so two writers racing on one key write the same bytes.
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in _MISSING:
                raise
        else:
            return False
        # An object-locked bucket needs an integrity header on every write.
        digest = hashlib.md5(body, usedforsecurity=False).digest()
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=media_type,
            ContentMD5=base64.b64encode(digest).decode("ascii"),
        )
        return True

    def get(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                return None
            raise
        return bytes(response["Body"].read())


class MemoryBlobStore:
    """For tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key: str, body: bytes, media_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = body
        return True

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)


def s3_store(settings: Settings | None = None) -> S3BlobStore:
    settings = settings or get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.blob_endpoint,
        region_name="us-east-1",
        aws_access_key_id=settings.blob_access_key.get_secret_value(),
        aws_secret_access_key=settings.blob_secret_key.get_secret_value(),
        config=Config(connect_timeout=5, read_timeout=60, retries={"max_attempts": 3}),
    )
    return S3BlobStore(client, settings.blob_bucket)
