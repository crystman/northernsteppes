"""Database access and migrations.

Migrations are plain .sql files applied once each, in filename order, inside a
transaction, with the applied set recorded in schema_migrations. That is
deliberately smaller than a migration framework: the schema is small, and a
volunteer maintainer should be able to read the whole mechanism in a minute.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_MIGRATIONS_TABLE = """
create table if not exists schema_migrations (
    filename   text primary key,
    applied_at timestamptz not null default now()
)
"""


async def connect(database_url: str) -> asyncpg.Pool:
    """Open a connection pool.

    Railway's DATABASE_URL uses the postgres:// scheme, which asyncpg accepts;
    no rewriting needed.
    """
    return await asyncpg.create_pool(database_url, min_size=1, max_size=5)


async def apply_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply any migration files not yet recorded. Returns those applied.

    Safe to run on every boot: already-applied files are skipped, so this is a
    no-op once the schema is current.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied: list[str] = []

    async with pool.acquire() as conn:
        await conn.execute(_MIGRATIONS_TABLE)
        done = {
            r["filename"]
            for r in await conn.fetch("select filename from schema_migrations")
        }
        for path in files:
            if path.name in done:
                continue
            # One transaction per file: a failure leaves the schema at the last
            # good migration rather than half-applied.
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    "insert into schema_migrations (filename) values ($1)",
                    path.name,
                )
            applied.append(path.name)

    return applied
