from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def make_engine(dsn: str) -> Engine:
    """Engine for the app role. It never connects as the schema owner, so grants hold.

    A short connect timeout makes readiness report an outage instead of hanging on it. Query
    parameters are kept out of error messages, because they can be a candidate's name or phone.
    """
    return create_engine(
        dsn, pool_pre_ping=True, hide_parameters=True, connect_args={"connect_timeout": 3}
    )
