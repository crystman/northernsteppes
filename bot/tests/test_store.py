"""Tests for database reads and writes behind the write commands.

Against a real Postgres, because the behaviour is largely in the SQL: the
ON CONFLICT that makes /dues idempotent, the composite foreign key that
rejects an unknown style, and the link handover that unlinks a previous owner.

The database is now the only place member records live, so `all_members()` is
not a merge over files any more -- it is the read side of everything the write
commands do, and most of these tests are a write followed by that read.

Set TEST_DATABASE_URL to run locally; CI supplies a service container.
"""

from __future__ import annotations

import os

import pytest

asyncpg = pytest.importorskip("asyncpg")

from conftest import requires_db, seed  # noqa: E402
from northernsteppes_bot import db  # noqa: E402
from northernsteppes_bot.store import (  # noqa: E402
    MemberStore,
    StoreError,
    UnknownMember,
    UnknownProficiency,
)

ACTOR = "1279000000000000000"


@pytest.fixture
async def store(sample_sheets):
    pool = await db.connect(os.environ["TEST_DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute("drop schema public cascade; create schema public;")
    await db.apply_migrations(pool)
    await seed(pool, sample_sheets)
    yield MemberStore(pool)
    await pool.close()


# --- dues ------------------------------------------------------------------

@requires_db
async def test_recording_dues_reports_that_it_happened(store):
    assert await store.record_dues("peasant", 2026, ACTOR) is True


@requires_db
async def test_recording_dues_twice_reports_no_change(store):
    """Two people entering the same member at a gathering should be told the
    second time was a no-op, not that they fixed something."""
    await store.record_dues("peasant", 2026, ACTOR)
    assert await store.record_dues("peasant", 2026, ACTOR) is False


@requires_db
async def test_recorded_dues_show_up_immediately(store):
    """The fix for the motivating bug, end to end."""
    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].paid_for(2026) is False

    await store.record_dues("peasant", 2026, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].paid_for(2026) is True


@requires_db
async def test_dues_for_an_unknown_member_is_rejected(store):
    with pytest.raises(UnknownMember):
        await store.record_dues("nobody", 2026, ACTOR)


@requires_db
async def test_dues_can_be_taken_back(store):
    """The correction path. Every other write command is a setter that can be
    called again; this one only ever inserted, so a mistake was permanent."""
    await store.record_dues("peasant", 2026, ACTOR)
    assert await store.forget_dues("peasant", 2026, ACTOR) is True

    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].paid_for(2026) is False


@requires_db
async def test_removing_dues_that_were_never_there_reports_no_change(store):
    """So a slip on the year says so, rather than looking like it worked."""
    assert await store.forget_dues("peasant", 2026, ACTOR) is False


@requires_db
async def test_removing_one_year_leaves_the_others(store):
    """peasant paid last year only. Removing this year -- which they never
    paid -- must not disturb the year they did."""
    await store.record_dues("peasant", 2026, ACTOR)
    await store.forget_dues("peasant", 2026, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].paid_for(2025) is True


@requires_db
async def test_removing_the_last_year_costs_the_member_their_rank(store):
    """Rank is gated on having ever paid, so this is a demotion, not just a
    display change. It is the consequence the command has to warn about."""
    from northernsteppes_bot.ranks import rank_name

    sheets = {s.slug: s for s in await store.all_members()}
    assert rank_name(sheets["savage"]) == "Savage"

    await store.forget_dues("savage", 2026, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert rank_name(sheets["savage"]) == "Peasant"


@requires_db
async def test_removing_dues_for_an_unknown_member_is_rejected(store):
    with pytest.raises(UnknownMember):
        await store.forget_dues("nobody", 2026, ACTOR)


@requires_db
async def test_dues_can_be_re_recorded_after_removal(store):
    """Removing must not leave anything behind that blocks the reinsert."""
    await store.record_dues("peasant", 2026, ACTOR)
    await store.forget_dues("peasant", 2026, ACTOR)
    assert await store.record_dues("peasant", 2026, ACTOR) is True


@requires_db
async def test_removing_dues_touches_only_that_member(store):
    await store.record_dues("peasant", 2026, ACTOR)
    await store.forget_dues("peasant", 2026, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["harbinger"].paid_for(2026) is True, (
        "somebody else's dues were deleted"
    )


# --- awards ----------------------------------------------------------------

@requires_db
async def test_award_returns_the_previous_level(store):
    previous = await store.set_proficiency("peasant", "weapon", "Archery", 3, ACTOR)
    assert previous == 0


@requires_db
async def test_award_is_visible_in_the_next_read(store):
    await store.set_proficiency("peasant", "weapon", "Archery", 3, ACTOR)
    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].weapon("Archery") == 3


@requires_db
async def test_award_rejects_an_unknown_style(store):
    """The composite foreign key, surfaced as something a command can explain."""
    with pytest.raises(UnknownProficiency):
        await store.set_proficiency("peasant", "weapon", "Sword and Board", 2, ACTOR)


@requires_db
async def test_professions_can_now_be_awarded(store):
    """They were deferred while the files still held them. With the files gone
    the database has to carry them, or they are simply lost."""
    await store.set_proficiency("peasant", "profession", "Cook", 2, ACTOR)
    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].professions["Cook"] == 2


@requires_db
async def test_award_rejects_an_unknown_profession(store):
    with pytest.raises(UnknownProficiency):
        await store.set_proficiency("peasant", "profession", "Beekeeper", 2, ACTOR)


@requires_db
async def test_a_profession_can_be_taken_back_to_zero(store):
    """The correction path for a profession awarded by mistake, the same way
    level 0 clears a weapon style."""
    await store.set_proficiency("peasant", "profession", "Cook", 2, ACTOR)
    await store.set_proficiency("peasant", "profession", "Cook", 0, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["peasant"].professions["Cook"] == 0


@requires_db
async def test_a_profession_reaches_harbinger(store):
    """The route the file deletion closed: professions gate Harbinger, and
    nothing could award one. savage has the four basic styles already, so
    veteran garb plus an Adept profession is the whole remaining gap."""
    from northernsteppes_bot.ranks import rank_name

    await store.set_flag("savage", "veteran_garb", True, ACTOR)
    sheets = {s.slug: s for s in await store.all_members()}
    assert rank_name(sheets["savage"]) == "Savage"

    await store.set_proficiency("savage", "profession", "Cook", 2, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert rank_name(sheets["savage"]) == "Harbinger"


@requires_db
async def test_a_profession_alone_reaches_savage(store):
    """The non-combat route: one proficient profession, no weapon styles.
    peasant has none, which is why they are a Peasant."""
    from northernsteppes_bot.ranks import rank_name

    await store.set_proficiency("peasant", "profession", "Brewer", 1, ACTOR)

    sheets = {s.slug: s for s in await store.all_members()}
    assert rank_name(sheets["peasant"]) == "Savage"


# --- flags -----------------------------------------------------------------

@requires_db
async def test_setting_a_flag_is_visible_in_the_next_read(store):
    await store.set_flag("harbinger", "veteran_garb", False, ACTOR)
    sheets = {s.slug: s for s in await store.all_members()}
    assert sheets["harbinger"].veteran_garb is False


@requires_db
async def test_an_arbitrary_field_name_is_refused(store):
    """Guards the one place a column name reaches SQL."""
    with pytest.raises(StoreError):
        await store.set_flag("harbinger", "archived; drop table members", True, ACTOR)


# --- linking ---------------------------------------------------------------

@requires_db
async def test_linking_then_looking_up_by_discord(store):
    await store.link_discord("harbinger", 42)
    assert await store.slug_for_discord(42) == "harbinger"


@requires_db
async def test_relinking_reports_who_lost_the_account(store):
    """Silently stealing the link would leave the previous member's /me
    broken with no explanation."""
    await store.link_discord("harbinger", 42)
    taken_from = await store.link_discord("savage", 42)
    assert taken_from == "harbinger"
    assert await store.slug_for_discord(42) == "savage"


@requires_db
async def test_linking_the_same_member_twice_is_not_a_handover(store):
    await store.link_discord("harbinger", 42)
    assert await store.link_discord("harbinger", 42) is None


# --- what a read returns ---------------------------------------------------

@requires_db
async def test_every_kind_survives_the_round_trip(store):
    """Weapons, professions and classes share one table, split by kind.
    Reading has to put each back where it came from."""
    merged = {s.slug: s for s in await store.all_members()}
    assert merged["harbinger"].weapons["Archery"] == 2
    assert merged["harbinger"].professions["Cook"] == 2
    assert merged["harbinger"].classes["Armor"] == 3


@requires_db
async def test_the_sync_state_table_is_gone(store):
    """Migration 003 drops it. Nothing renders files any more, so a "changes
    are waiting" flag has nothing to mean."""
    async with store.pool.acquire() as conn:
        assert not await conn.fetchval("select to_regclass('public.sync_state')")


@requires_db
async def test_a_member_deleted_from_the_database_is_gone(store):
    """There is no file to fall back on any more, and pretending otherwise
    would show a member nobody can edit."""
    async with store.pool.acquire() as conn:
        await conn.execute("delete from members where slug = 'harbinger'")
    merged = {s.slug: s for s in await store.all_members()}
    assert "harbinger" not in merged


@requires_db
async def test_known_proficiencies_lists_the_seeded_styles(store):
    names = await store.known_proficiencies("weapon")
    assert len(names) == 11
    assert "Sword & Board" in names


@requires_db
async def test_known_proficiencies_covers_professions_too(store):
    assert "Foamsmith" in await store.known_proficiencies("profession")


@requires_db
async def test_units_are_carried_through(store):
    merged = {s.slug: s for s in await store.all_members()}
    assert merged["savage"].units == ["CoWS"]
    assert merged["harbinger"].units == []


# --- creating members ------------------------------------------------------

@requires_db
async def test_creating_a_member_makes_them_visible(store):
    """A member created only in the database must appear in commands at once,
    or /member-add looks like it did nothing."""
    from northernsteppes_bot.store import create_member

    slug = await create_member(store, "Test Newcomer", None, ACTOR)
    assert slug == "test-newcomer"

    merged = {s.slug: s for s in await store.all_members()}
    assert "test-newcomer" in merged
    assert merged["test-newcomer"].display_name == "Test Newcomer"


@requires_db
async def test_created_members_start_unranked(store):
    from northernsteppes_bot.ranks import rank_name
    from northernsteppes_bot.store import create_member

    await create_member(store, "Test Newcomer", None, ACTOR)
    merged = {s.slug: s for s in await store.all_members()}
    assert rank_name(merged["test-newcomer"]) == "Unranked"


@requires_db
async def test_duplicate_slug_is_rejected(store):
    from northernsteppes_bot.store import DuplicateMember, create_member

    with pytest.raises(DuplicateMember):
        await create_member(store, "Another Harbinger", "harbinger", ACTOR)


@requires_db
async def test_invalid_slug_is_rejected(store):
    """Slugs reach URLs, and used to reach file names."""
    from northernsteppes_bot.store import InvalidSlug, create_member

    for bad in ["../escape", "Has Spaces", "trailing-", "sym$bol", "-"]:
        with pytest.raises(InvalidSlug):
            await create_member(store, "X", bad, ACTOR)


@requires_db
async def test_an_empty_slug_falls_back_to_the_name(store):
    """Discord sends "" for an omitted optional string, which has to mean
    "derive it" rather than being an error."""
    from northernsteppes_bot.store import create_member

    assert await create_member(store, "Test Newcomer", "", ACTOR) == "test-newcomer"


@requires_db
async def test_an_explicit_slug_is_lowercased_rather_than_refused(store):
    """Case is normalised, not rejected: slugs are lowercase by convention and
    correcting it is friendlier than an error."""
    from northernsteppes_bot.store import create_member

    assert await create_member(store, "Test Newcomer", "Fresh", ACTOR) == "fresh"


# --- /me resolving through the link ----------------------------------------

@requires_db
async def test_own_sheet_flow_after_linking(store):
    """What /me does: Discord id -> slug -> sheet."""
    await store.link_discord("harbinger", 4242)
    slug = await store.slug_for_discord(4242)
    assert slug == "harbinger"

    merged = {s.slug: s for s in await store.all_members()}
    assert merged[slug].display_name == "Harriet Harbinger"


@requires_db
async def test_unlinked_discord_id_resolves_to_nothing(store):
    assert await store.slug_for_discord(999999) is None


@requires_db
async def test_link_survives_other_writes(store):
    """Recording dues must not disturb the Discord mapping."""
    await store.link_discord("harbinger", 4242)
    await store.record_dues("harbinger", 2026, ACTOR)
    await store.set_flag("harbinger", "veteran_garb", False, ACTOR)
    assert await store.slug_for_discord(4242) == "harbinger"


# --- an empty database -----------------------------------------------------

@requires_db
async def test_writes_fail_clearly_when_the_database_is_empty(store):
    """The deployment bug: migrations had run but nothing had been imported,
    so every write failed looking up a member who was plainly on the roster."""
    async with store.pool.acquire() as conn:
        await conn.execute("delete from members")

    with pytest.raises(UnknownMember):
        await store.record_dues("harbinger", 2026, ACTOR)


@requires_db
async def test_reads_on_an_empty_database_answer_rather_than_fail(store):
    """Day one of a fresh deployment: the roster should say there is nobody,
    not raise."""
    from northernsteppes_bot import views

    async with store.pool.acquire() as conn:
        await conn.execute("delete from members")

    assert await store.all_members() == []
    assert "Nobody has 2026 dues recorded yet" in views.format_roster([], 2026)


# --- what the read commands see after a write ------------------------------

@requires_db
async def test_roster_shows_a_member_immediately_after_recording_dues(store):
    """The bug a live test found: /dues succeeded, then /roster still showed
    nobody current, because the read commands were using the file cache and
    ignoring the database entirely."""
    from northernsteppes_bot import views

    await store.record_dues("peasant", 2026, ACTOR)
    roster = views.format_roster(await store.all_members(), 2026)

    current = roster.split("Last year's members")[0]
    assert "Pat Peasant" in current, "recorded dues should move them to current"


@requires_db
async def test_rank_reflects_recorded_dues_immediately(store):
    from northernsteppes_bot import views

    before = {s.slug: s for s in await store.all_members()}
    assert "Dues not up to date" in views.format_rank(before["peasant"])

    await store.record_dues("peasant", 2026, ACTOR)

    after = {s.slug: s for s in await store.all_members()}
    assert "Dues paid for 2026" in views.format_rank(after["peasant"])


@requires_db
async def test_an_award_is_visible_immediately(store):
    from northernsteppes_bot import views

    await store.set_proficiency("peasant", "weapon", "Archery", 3, ACTOR)
    after = {s.slug: s for s in await store.all_members()}
    assert "Archery — Master" in views.format_sheet(after["peasant"])
