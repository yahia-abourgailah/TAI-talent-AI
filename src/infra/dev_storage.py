"""Creates the raw-capture bucket, with object lock, on a dev machine.

Staging and production buckets are provisioned by infrastructure (DEP-05); this refuses to run
there. Usage: ``python -m infra.dev_storage``
"""

import sys

from botocore.exceptions import ClientError

from config import Environment, get_settings
from infra.probes import s3_client


def main() -> int:
    settings = get_settings()
    if settings.env is not Environment.DEV:
        print(f"Refusing to create buckets with TALENT_ENV={settings.env}.", file=sys.stderr)
        return 1

    client = s3_client(settings)
    try:
        client.head_bucket(Bucket=settings.blob_bucket)
    except ClientError:
        # Object lock can only be switched on when a bucket is created (BR-107).
        client.create_bucket(Bucket=settings.blob_bucket, ObjectLockEnabledForBucket=True)
        print(f"Created bucket {settings.blob_bucket} with object lock.")
    else:
        print(f"Bucket {settings.blob_bucket} already exists.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
