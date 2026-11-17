"""The connection pool (D27) and the FastAPI dependency that lends out connections."""

from collections.abc import Iterator

from fastapi import Request
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from mind_dump.config import Settings


def create_pool(settings: Settings) -> ConnectionPool[Connection[DictRow]]:
    return ConnectionPool(
        settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        kwargs={"row_factory": dict_row},  # rows come back as {"column": value}
        check=ConnectionPool.check_connection,  # drop dead connections before lending them
        open=False,  # opened in the app's lifespan, so import never touches the network
        name="api",
    )


def get_conn(request: Request) -> Iterator[Connection[DictRow]]:
    """Borrow a connection for one request; commit on success, roll back on error, return it."""
    pool: ConnectionPool[Connection[DictRow]] = request.app.state.pool
    with pool.connection() as conn:
        yield conn
