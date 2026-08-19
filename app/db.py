"""Database engine setup.

CONNECTION STRINGS: the app uses DATABASE_URL (POOLED, has -pooler in the
hostname). Alembic migrations use DATABASE_URL_DIRECT (no -pooler) - see
migrations/env.py. Confusing them causes "relation does not exist".

Both URLs as stored in .env carry libpq-only query params (sslmode=,
channel_binding=) that asyncpg's connect() does not understand (verified:
asyncpg.connect's real signature has `ssl`, not `sslmode`, and no
`channel_binding` at all). Stripped and replaced with connect_args in
_to_asyncpg_url below - confirmed against the live Neon DB before use.

asyncpg through a transaction-mode pooler needs the prepared-statement
cache disabled. SQLAlchemy's asyncpg dialect exposes this as the DBAPI
query parameter `prepared_statement_cache_size` (NOT the raw asyncpg
`statement_cache_size` kwarg) - verified in
sqlalchemy/dialects/postgresql/asyncpg.py before use.
"""

import os

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _to_asyncpg_url(raw_url: str, *, disable_prepared_cache: bool = False) -> str:
    base = raw_url.split("?", 1)[0]
    url = base.replace("postgresql://", "postgresql+asyncpg://", 1)
    if disable_prepared_cache:
        url += "?prepared_statement_cache_size=0"
    return url


def make_app_engine() -> AsyncEngine:
    """POOLED (transaction-mode pgbouncer). Small app-side pool; let
    PgBouncer multiplex."""
    url = _to_asyncpg_url(os.environ["DATABASE_URL"], disable_prepared_cache=True)
    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"ssl": "require"},
    )
