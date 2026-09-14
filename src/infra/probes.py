"""Readiness checks: one per dependency the API needs before it can serve a request."""

from collections.abc import Callable
from typing import Any

import boto3
import redis
from botocore.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from config import Settings
from db import make_engine

Probe = Callable[[], None]


def database_probe(engine: Engine) -> Probe:
    def check() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    return check


def cache_probe(url: str) -> Probe:
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)

    def check() -> None:
        client.ping()

    return check


def s3_client(settings: Settings) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.blob_endpoint,
        region_name="us-east-1",
        aws_access_key_id=settings.blob_access_key.get_secret_value(),
        aws_secret_access_key=settings.blob_secret_key.get_secret_value(),
        config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}),
    )


def object_storage_probe(settings: Settings) -> Probe:
    client = s3_client(settings)

    def check() -> None:
        client.head_bucket(Bucket=settings.blob_bucket)

    return check


def default_probes(settings: Settings) -> dict[str, Probe]:
    return {
        "database": database_probe(make_engine(settings.db_dsn.get_secret_value())),
        "cache": cache_probe(settings.redis_url.get_secret_value()),
        "object_storage": object_storage_probe(settings),
    }
