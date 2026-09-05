"""Tests for the bootstrap import.

These need a real Postgres, because the behaviour under test is largely in the
SQL: the ON CONFLICT upserts and the xmax trick that distinguishes an insert
from an update. Faking the database would test the fake.

Set TEST_DATABASE_URL to run them locally; CI supplies a service container.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from northernsteppes_bot import db  # noqa: E402
from northernsteppes_bot.importer import (  # noqa: E402
    BOOTSTRAP_ACTOR,
    bootstrap,
    proficiency_rows,
)
from northernsteppes_bot.members import load_all  # noqa: E402
from northernsteppes_bot.ranks import MemberSheet  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"

# No module-level asyncio mark: pytest.ini sets asyncio_mode = auto, which
# picks up async tests on its own and leaves the sync ones alone.


def _database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or None


requires_db = pytest.mark.skipif(
    _database_url() is None,
    reason="set TEST_DATABASE_URL to run database tests",
)


@pytest.fixture
async def pool():
    url = _database_url()
    pool = await db.connect(url)
    # Start from a clean schema so tests never depend on each other's rows.
    async with pool.acquire() as conn:
        await conn.execute(
            "drop schema public cascade; create schema public;"
        )
    await db.apply_migrations(pool)
    yield pool
    await pool.close()


# --- pure helpers, no database needed --------------------------------------

def test_only_weapon_rows_are_imported():
    """Classes, professions and the class counters/flags are being reworked,
    so nothing defines them and nothing may be assigned. They are still parsed
    off the sheet -- rank() needs them -- just not persisted."""
    sheet = MemberSheet(
        slug="x",
        weapons={"Flail": 2, "Rock": 1},
        professions={"Cook": 1},
        classes={"Steal_10": True, "Armor": 3, "Scout": 1},
    )
    rows = proficiency_rows(sheet)
    assert {k for k, _, _ in rows} == {"weapon"}
    assert rows == [("weapon", "Flail", 2), ("weapon", "Rock", 1)]


# --- against a real database ----------------------------------------------

@requires_db
async def test_migrations_are_idempotent(pool):
    """apply_migrations runs on every boot, so a second run must do nothing."""
    again = await db.apply_migrations(pool)
    assert again == []


@requires_db
async def test_bootstrap_imports_every_member(pool):
    result = await bootstrap(pool, MEMBERS_DIR)
    expected = len(load_all(MEMBERS_DIR))
    assert result.members_inserted == expected
    assert result.members_updated == 0

    async with pool.acquire() as conn:
        count = await conn.fetchval("select count(*) from members")
    assert count == expected


@requires_db
async def test_bootstrap_is_idempotent(pool):
    """Runs on every boot until the sync job exists; must not duplicate rows
    or manufacture history."""
    first = await bootstrap(pool, MEMBERS_DIR)
    second = await bootstrap(pool, MEMBERS_DIR)

    assert second.members_inserted == 0
    assert second.members_updated == first.members_inserted

    async with pool.acquire() as conn:
        members = await conn.fetchval("select count(*) from members")
        profs = await conn.fetchval("select count(*) from proficiencies")
    assert members == first.members_inserted
    assert profs == first.proficiency_rows


@requires_db
async def test_imported_dues_are_marked_as_bootstrap(pool):
    await bootstrap(pool, MEMBERS_DIR)
    async with pool.acquire() as conn:
        actors = await conn.fetch("select distinct recorded_by from dues_paid")
    assert [r["recorded_by"] for r in actors] == [BOOTSTRAP_ACTOR]


@requires_db
async def test_roster_matches_the_files(pool):
    """The database should reproduce the files it was built from."""
    await bootstrap(pool, MEMBERS_DIR)
    sheets = {s.slug: s for s in load_all(MEMBERS_DIR)}

    async with pool.acquire() as conn:
        rows = await conn.fetch("select slug, display_name, waiver from members")

    assert {r["slug"] for r in rows} == set(sheets)
    for r in rows:
        assert r["display_name"] == sheets[r["slug"]].display_name
        assert r["waiver"] == sheets[r["slug"]].waiver


@requires_db
async def test_no_2026_dues_present(pool):
    """Pins the bug that motivates the bot: nobody has current-year dues, which
    is why the site's roster is empty. Should start failing once /dues exists
    and leadership records them -- at which point delete this test."""
    await bootstrap(pool, MEMBERS_DIR)
    async with pool.acquire() as conn:
        paid_2026 = await conn.fetchval(
            "select count(*) from dues_paid where year = 2026"
        )
    assert paid_2026 == 0


@requires_db
async def test_unknown_proficiency_is_rejected(pool):
    """The composite foreign key is the point of proficiency_defs: a typo in a
    command must fail loudly rather than create a proficiency the site
    templates will never render."""
    await bootstrap(pool, MEMBERS_DIR)
    async with pool.acquire() as conn:
        member_id = await conn.fetchval("select id from members limit 1")
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "insert into proficiencies (member_id, kind, name, level)"
                " values ($1, 'weapon', 'Sword and Board', 2)",
                member_id,
            )


@requires_db
async def test_deferred_kinds_cannot_be_assigned(pool):
    """The point of deferring: with no definitions for classes, professions,
    counters or flags, the foreign key makes it impossible to assign one and
    then have to clean it up when the rework lands."""
    await bootstrap(pool, MEMBERS_DIR)
    async with pool.acquire() as conn:
        member_id = await conn.fetchval("select id from members limit 1")
        for kind, name in [
            ("profession", "Cook"),
            ("class", "Scout"),
            ("counter", "Armor"),
            ("flag", "Steal_10"),
            ("profession", "Flail"),   # real name, wrong kind
        ]:
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    "insert into proficiencies (member_id, kind, name, level)"
                    " values ($1, $2, $3, 1)",
                    member_id, kind, name,
                )


@requires_db
async def test_only_weapons_present_after_bootstrap(pool):
    await bootstrap(pool, MEMBERS_DIR)
    async with pool.acquire() as conn:
        kinds = await conn.fetch("select distinct kind from proficiencies")
    assert [r["kind"] for r in kinds] == ["weapon"]


@requires_db
async def test_units_are_imported_from_the_legacy_singular_key(pool):
    """Member files still say Unit = "CoWS"; the column is plural. The value
    has to survive the rename, not be dropped by it."""
    await bootstrap(pool, MEMBERS_DIR)
    async with pool.acquire() as conn:
        rows = {
            r["slug"]: r["units"]
            for r in await conn.fetch("select slug, units from members")
        }
    assert rows["kaigar"] == ["CoWS"]
    assert rows["meatwolf"] == ["BoTF"]
    assert rows["lamp"] == [], "members with no unit should have an empty array"
