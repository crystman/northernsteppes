"""Tests for database reads and writes behind the write commands.

Against a real Postgres, because the behaviour is largely in the SQL: the
ON CONFLICT that makes /dues idempotent, the composite foreign key that
rejects an unknown style, and the link handover that unlinks a previous owner.

Set TEST_DATABASE_URL to run locally; CI supplies a service container.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

from northernsteppes_bot import db  # noqa: E402
from northernsteppes_bot.importer import bootstrap  # noqa: E402
from northernsteppes_bot.members import load_all  # noqa: E402
from northernsteppes_bot.store import (  # noqa: E402
    MemberStore,
    StoreError,
    UnknownMember,
    UnknownProficiency,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"

ACTOR = "1279000000000000000"

requires_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database tests",
)


@pytest.fixture
async def store():
    pool = await db.connect(os.environ["TEST_DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute("drop schema public cascade; create schema public;")
    await db.apply_migrations(pool)
    await bootstrap(pool, MEMBERS_DIR)
    yield MemberStore(pool)
    await pool.close()


# --- dues ------------------------------------------------------------------

@requires_db
async def test_recording_dues_reports_that_it_happened(store):
    assert await store.record_dues("lamp", 2026, ACTOR) is True


@requires_db
async def test_recording_dues_twice_reports_no_change(store):
    """Two people entering the same member at a gathering should be told the
    second time was a no-op, not that they fixed something."""
    await store.record_dues("lamp", 2026, ACTOR)
    assert await store.record_dues("lamp", 2026, ACTOR) is False


@requires_db
async def test_recorded_dues_show_up_in_the_overlay(store):
    """The fix for the motivating bug, end to end."""
    sheets = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert sheets["lamp"].paid_for(2026) is False

    await store.record_dues("lamp", 2026, ACTOR)

    sheets = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert sheets["lamp"].paid_for(2026) is True


@requires_db
async def test_dues_for_an_unknown_member_is_rejected(store):
    with pytest.raises(UnknownMember):
        await store.record_dues("nobody", 2026, ACTOR)


@requires_db
async def test_writing_dues_marks_the_sync_dirty(store):
    async with store.pool.acquire() as conn:
        assert await conn.fetchval("select dirty_since from sync_state") is None
    await store.record_dues("lamp", 2026, ACTOR)
    async with store.pool.acquire() as conn:
        assert await conn.fetchval("select dirty_since from sync_state") is not None


@requires_db
async def test_a_no_op_write_does_not_mark_dirty(store):
    """Otherwise the sync job would commit nothing, repeatedly."""
    await store.record_dues("lamp", 2026, ACTOR)
    async with store.pool.acquire() as conn:
        await conn.execute("update sync_state set dirty_since = null")
    await store.record_dues("lamp", 2026, ACTOR)
    async with store.pool.acquire() as conn:
        assert await conn.fetchval("select dirty_since from sync_state") is None


# --- awards ----------------------------------------------------------------

@requires_db
async def test_award_returns_the_previous_level(store):
    previous = await store.set_proficiency("lamp", "weapon", "Archery", 3, ACTOR)
    assert previous == 0


@requires_db
async def test_award_updates_the_overlay(store):
    await store.set_proficiency("lamp", "weapon", "Archery", 3, ACTOR)
    sheets = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert sheets["lamp"].weapon("Archery") == 3


@requires_db
async def test_award_rejects_an_unknown_style(store):
    """The composite foreign key, surfaced as something a command can explain."""
    with pytest.raises(UnknownProficiency):
        await store.set_proficiency("lamp", "weapon", "Sword and Board", 2, ACTOR)


@requires_db
async def test_award_rejects_a_deferred_kind(store):
    with pytest.raises(UnknownProficiency):
        await store.set_proficiency("lamp", "profession", "Cook", 2, ACTOR)


# --- flags -----------------------------------------------------------------

@requires_db
async def test_setting_a_flag_shows_in_the_overlay(store):
    await store.set_flag("lamp", "veteran_garb", False, ACTOR)
    sheets = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert sheets["lamp"].veteran_garb is False


@requires_db
async def test_an_arbitrary_field_name_is_refused(store):
    """Guards the one place a column name reaches SQL."""
    with pytest.raises(StoreError):
        await store.set_flag("lamp", "archived; drop table members", True, ACTOR)


# --- linking ---------------------------------------------------------------

@requires_db
async def test_linking_then_looking_up_by_discord(store):
    await store.link_discord("lamp", 42)
    assert await store.slug_for_discord(42) == "lamp"


@requires_db
async def test_relinking_reports_who_lost_the_account(store):
    """Silently stealing the link would leave the previous member's /me
    broken with no explanation."""
    await store.link_discord("lamp", 42)
    taken_from = await store.link_discord("goose", 42)
    assert taken_from == "lamp"
    assert await store.slug_for_discord(42) == "goose"


@requires_db
async def test_linking_the_same_member_twice_is_not_a_handover(store):
    await store.link_discord("lamp", 42)
    assert await store.link_discord("lamp", 42) is None


# --- the overlay -----------------------------------------------------------

@requires_db
async def test_overlay_keeps_file_only_fields(store):
    """Professions and classes are deferred from the database; the overlay
    must not blank them."""
    from_files = {s.slug: s for s in load_all(MEMBERS_DIR)}
    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert merged["lamp"].professions == from_files["lamp"].professions
    assert merged["lamp"].classes == from_files["lamp"].classes


@requires_db
async def test_overlay_passes_through_members_the_database_lacks(store):
    """Better slightly stale than "no such member" about somebody plainly on
    the website."""
    async with store.pool.acquire() as conn:
        await conn.execute("delete from members where slug = 'lamp'")
    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert "lamp" in merged


@requires_db
async def test_known_proficiencies_lists_the_seeded_styles(store):
    names = await store.known_proficiencies("weapon")
    assert len(names) == 11
    assert "Sword & Board" in names


@requires_db
async def test_overlay_carries_units_through(store):
    """The overlay owns units, and the renderer writes whatever it carries.
    If the overlay dropped them, a later sync would blank kaigar's unit --
    the same silent data loss the renderer's own tests guard against.
    """
    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert merged["kaigar"].units == ["CoWS"]
    assert merged["lamp"].units == []


# --- end to end ------------------------------------------------------------

@requires_db
async def test_dues_command_path_produces_a_one_line_diff(store):
    """/dues -> database -> overlay -> rendered file.

    The whole point of the project: recording dues in Discord should turn
    into a minimal, reviewable commit.
    """
    import difflib
    from northernsteppes_bot.renderer import render_member

    path = MEMBERS_DIR / "_kimba.md"
    before = path.read_text(encoding="utf-8")

    await store.record_dues("kimba", 2026, ACTOR)
    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    after = render_member(before, merged["kimba"])

    added = [
        line for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert added == ["+2026 = true"], f"expected one added line, got {added}"


@requires_db
async def test_award_command_path_changes_one_line(store):
    import difflib
    from northernsteppes_bot.renderer import render_member

    path = MEMBERS_DIR / "_kaigar.md"
    before = path.read_text(encoding="utf-8")

    await store.set_proficiency("kaigar", "weapon", "Flail", 2, ACTOR)
    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    after = render_member(before, merged["kaigar"])

    changed = [
        line for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    # Flail changes; Race and Unit are retired by the renderer, and Units is
    # written in their place.
    assert any("Flail" in line for line in changed)
    assert any('Units = ["CoWS"]' in line for line in changed), (
        "unit must survive the round trip through the database"
    )


# --- creating members ------------------------------------------------------

@requires_db
async def test_creating_a_member_makes_them_visible(store):
    """A member created only in the database must still appear in commands,
    or /member-add looks like it did nothing until the sync runs."""
    from northernsteppes_bot.store import create_member

    slug = await create_member(store, "Test Newcomer", None, ACTOR)
    assert slug == "test-newcomer"

    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert "test-newcomer" in merged
    assert merged["test-newcomer"].display_name == "Test Newcomer"


@requires_db
async def test_created_members_start_unranked(store):
    from northernsteppes_bot.ranks import rank_name
    from northernsteppes_bot.store import create_member

    await create_member(store, "Test Newcomer", None, ACTOR)
    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert rank_name(merged["test-newcomer"]) == "Unranked"


@requires_db
async def test_duplicate_slug_is_rejected(store):
    from northernsteppes_bot.store import DuplicateMember, create_member

    with pytest.raises(DuplicateMember):
        await create_member(store, "Lamp Again", "lamp", ACTOR)


@requires_db
async def test_invalid_slug_is_rejected(store):
    """Slugs become file names and URLs."""
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
    """Case is normalised, not rejected: the file convention is lowercase and
    correcting it is friendlier than an error."""
    from northernsteppes_bot.store import create_member

    assert await create_member(store, "Test Newcomer", "Newcomer", ACTOR) == "newcomer"


@requires_db
async def test_creating_a_member_marks_the_sync_dirty(store):
    from northernsteppes_bot.store import create_member

    async with store.pool.acquire() as conn:
        await conn.execute("update sync_state set dirty_since = null")
    await create_member(store, "Test Newcomer", None, ACTOR)
    async with store.pool.acquire() as conn:
        assert await conn.fetchval("select dirty_since from sync_state") is not None


# --- sync status -----------------------------------------------------------

@requires_db
async def test_sync_status_reports_clean_then_dirty(store):
    from northernsteppes_bot.store import sync_status

    async with store.pool.acquire() as conn:
        await conn.execute("update sync_state set dirty_since = null")
    assert (await sync_status(store))["dirty_since"] is None

    await store.record_dues("lamp", 2026, ACTOR)
    status = await sync_status(store)
    assert status["dirty_since"] is not None
    assert status["members"] == len(load_all(MEMBERS_DIR))


# --- /me resolving through the link ----------------------------------------

@requires_db
async def test_own_sheet_flow_after_linking(store):
    """What /me does: Discord id -> slug -> sheet."""
    await store.link_discord("lamp", 4242)
    slug = await store.slug_for_discord(4242)
    assert slug == "lamp"

    merged = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert merged[slug].display_name == "Lamp"


@requires_db
async def test_unlinked_discord_id_resolves_to_nothing(store):
    assert await store.slug_for_discord(999999) is None


@requires_db
async def test_link_survives_other_writes(store):
    """Recording dues must not disturb the Discord mapping."""
    await store.link_discord("lamp", 4242)
    await store.record_dues("lamp", 2026, ACTOR)
    await store.set_flag("lamp", "veteran_garb", False, ACTOR)
    assert await store.slug_for_discord(4242) == "lamp"


# --- the bug: a schema with no members -------------------------------------

@requires_db
async def test_writes_fail_clearly_when_the_database_is_empty(store):
    """The deployment bug: migrations had run but the import had not, so every
    write failed looking up a member who was plainly on the roster."""
    from northernsteppes_bot.store import UnknownMember

    async with store.pool.acquire() as conn:
        await conn.execute("delete from members")

    with pytest.raises(UnknownMember):
        await store.record_dues("lamp", 2026, ACTOR)


@requires_db
async def test_bootstrap_from_sheets_populates_an_empty_database(store):
    """The fix: the bot imports what it loaded, whatever the source, so a host
    with no members directory can still seed itself."""
    from northernsteppes_bot.importer import bootstrap_sheets

    async with store.pool.acquire() as conn:
        await conn.execute("delete from members")

    sheets = load_all(MEMBERS_DIR)
    result = await bootstrap_sheets(store.pool, sheets)
    assert result.members_inserted == len(sheets)

    assert await store.record_dues("lamp", 2026, ACTOR) is True


@requires_db
async def test_bootstrap_from_sheets_is_idempotent(store):
    """It runs on every boot."""
    from northernsteppes_bot.importer import bootstrap_sheets

    sheets = load_all(MEMBERS_DIR)
    again = await bootstrap_sheets(store.pool, sheets)
    assert again.members_inserted == 0
    assert again.members_updated == len(sheets)


# --- what the read commands see after a write ------------------------------

@requires_db
async def test_roster_shows_a_member_immediately_after_recording_dues(store):
    """The bug a live test found: /dues succeeded, then /roster still showed
    nobody current, because the read commands were using the file cache and
    ignoring the database entirely."""
    from northernsteppes_bot import views

    await store.record_dues("lamp", 2026, ACTOR)
    overlaid = await store.overlay(load_all(MEMBERS_DIR))

    roster = views.format_roster(overlaid, 2026)
    current = roster.split("Last year's members")[0]
    assert "Lamp" in current, "recorded dues should move Lamp to current"
    assert "**Current members (2026)** — 1" in roster
    assert "**Harbinger** — Lamp" in roster


@requires_db
async def test_rank_reflects_recorded_dues_immediately(store):
    from northernsteppes_bot import views

    before = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert "Dues not up to date" in views.format_rank(before["lamp"])

    await store.record_dues("lamp", 2026, ACTOR)

    after = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert "Dues paid for 2026" in views.format_rank(after["lamp"])


@requires_db
async def test_an_award_is_visible_immediately(store):
    from northernsteppes_bot import views

    await store.set_proficiency("lamp", "weapon", "Archery", 3, ACTOR)
    after = {s.slug: s for s in await store.overlay(load_all(MEMBERS_DIR))}
    assert "Archery — Master" in views.format_sheet(after["lamp"])


@requires_db
async def test_reads_without_a_database_still_work(store):
    """The overlay is an improvement on the files, not a replacement: with no
    database the commands must still answer."""
    from northernsteppes_bot import views

    sheets = load_all(MEMBERS_DIR)
    roster = views.format_roster(sheets, 2026)
    assert "Nobody has 2026 dues recorded yet" in roster
