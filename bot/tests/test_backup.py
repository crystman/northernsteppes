"""Tests for the database snapshot and restore.

With the member files gone, this is the only way member records leave the
database. The properties that matter are that a round trip loses nothing, and
that restoring an old snapshot over a live database cannot silently delete
members recorded since it was taken.
"""

from __future__ import annotations

import os

import pytest

asyncpg = pytest.importorskip("asyncpg")

from conftest import requires_db, seed  # noqa: E402
from northernsteppes_bot import db  # noqa: E402
from northernsteppes_bot.backup import dump, restore  # noqa: E402


@pytest.fixture
async def pool(sample_sheets):
    pool = await db.connect(os.environ["TEST_DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute("drop schema public cascade; create schema public;")
    await db.apply_migrations(pool)
    await seed(pool, sample_sheets)
    yield pool
    await pool.close()


@requires_db
async def test_a_round_trip_loses_nothing(pool):
    before = await dump(pool)
    async with pool.acquire() as conn:
        await conn.execute("delete from members")

    await restore(pool, before)
    after = await dump(pool)

    for table in ("members", "dues_paid", "proficiencies"):
        assert len(after[table]) == len(before[table]), f"{table} lost rows"


@requires_db
async def test_slugs_survive_the_round_trip(pool):
    before = await dump(pool)
    slugs = {m["slug"] for m in before["members"]}
    async with pool.acquire() as conn:
        await conn.execute("delete from members")
    await restore(pool, before)

    after = await dump(pool)
    assert {m["slug"] for m in after["members"]} == slugs


@requires_db
async def test_restore_does_not_delete_newer_members(pool):
    """Restoring an old snapshot must not silently drop somebody recorded
    after it was taken. Emptying the database is a separate, deliberate act."""
    snapshot = await dump(pool)

    async with pool.acquire() as conn:
        await conn.execute(
            "insert into members (slug, display_name)"
            " values ('later-arrival', 'Later Arrival')"
        )

    await restore(pool, snapshot)

    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "select exists(select 1 from members where slug = 'later-arrival')"
        )


@requires_db
async def test_restore_is_idempotent(pool):
    snapshot = await dump(pool)
    await restore(pool, snapshot)
    await restore(pool, snapshot)
    async with pool.acquire() as conn:
        assert await conn.fetchval("select count(*) from members") == len(
            snapshot["members"]
        )


@requires_db
async def test_restore_updates_changed_values(pool):
    snapshot = await dump(pool)
    async with pool.acquire() as conn:
        await conn.execute("update members set waiver = false")
    await restore(pool, snapshot)

    after = {m["slug"]: m for m in (await dump(pool))["members"]}
    original = {m["slug"]: m for m in snapshot["members"]}
    for slug, row in original.items():
        assert after[slug]["waiver"] == row["waiver"]


# --- reaching the deployed database ----------------------------------------

def test_a_public_url_is_preferred_over_the_internal_one(monkeypatch):
    """`railway run` hands out the service's own DATABASE_URL, which points at
    postgres.railway.internal -- a name that only resolves inside Railway.
    A backup taken from a laptop otherwise fails with a bare DNS error."""
    from northernsteppes_bot.backup import _reachable_database_url

    monkeypatch.setenv("DATABASE_URL", "postgresql://x@postgres.railway.internal/r")
    monkeypatch.setenv("DATABASE_PUBLIC_URL", "postgresql://x@shed.proxy.rlwy.net/r")
    assert "proxy" in _reachable_database_url()


def test_the_internal_url_is_used_when_there_is_no_public_one(monkeypatch):
    """Which is right for the bot's own host, where internal is the only one
    that resolves."""
    from northernsteppes_bot.backup import _reachable_database_url

    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@postgres.railway.internal/r")
    assert "internal" in _reachable_database_url()


def test_no_url_at_all_resolves_to_nothing(monkeypatch):
    from northernsteppes_bot.backup import _reachable_database_url

    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _reachable_database_url() is None


def test_an_unknown_format_is_refused():
    import asyncio
    with pytest.raises(SystemExit):
        asyncio.run(restore(None, {"format": 99}))
