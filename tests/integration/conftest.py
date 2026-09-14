import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _engine(variable: str) -> Engine:
    dsn = os.environ.get(variable)
    if not dsn:
        pytest.skip(f"{variable} is not set; integration tests need a migrated database")
    return create_engine(dsn)


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    return _engine("TALENT_TEST_OWNER_DSN")


@pytest.fixture(scope="session")
def app_engine() -> Engine:
    return _engine("TALENT_TEST_APP_DSN")
