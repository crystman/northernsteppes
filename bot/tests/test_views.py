"""Tests for command output.

These run against the real member files, because the thing worth checking is
that what a member would actually see is correct and readable. Assertions are
about structure and invariants rather than exact wording, so rephrasing a
message does not break the suite.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from northernsteppes_bot import views
from northernsteppes_bot.ranks import MemberSheet, RANK_NAMES, gaps, rank


@pytest.fixture
def sheets(sample_sheets):
    return sample_sheets


# --- truncation ------------------------------------------------------------

def test_short_text_is_untouched():
    assert views.truncate("hello") == "hello"


def test_long_text_is_trimmed_and_says_so():
    out = views.truncate("\n".join(f"line {i}" for i in range(500)))
    assert len(out) <= views.MESSAGE_LIMIT
    assert out.endswith("… truncated")


def test_truncation_does_not_split_a_line():
    out = views.truncate("\n".join(f"line {i}" for i in range(500)))
    body = out[: -len("\n… truncated")]
    assert all(line.startswith("line ") for line in body.split("\n") if line)


# --- every real member renders --------------------------------------------

@pytest.mark.parametrize("view", [views.format_rank, views.format_gaps,
                                  views.format_sheet])
def test_every_member_renders_within_limits(sheets, view):
    for s in sheets:
        out = view(s)
        assert out, f"{s.slug} produced empty output from {view.__name__}"
        assert len(out) <= views.MESSAGE_LIMIT


def test_rank_output_names_the_member_and_rank(sheets):
    for s in sheets:
        out = views.format_rank(s)
        assert (s.display_name or s.slug) in out
        assert RANK_NAMES[rank(s)] in out


def test_rank_output_explains_itself(sheets):
    """The value of /rank over the website is that it says *why*."""
    for s in sheets:
        assert "**Why**" in views.format_rank(s)


def test_gaps_lists_every_gap(sheets):
    for s in sheets:
        out = views.format_gaps(s)
        for g in gaps(s):
            assert g in out


def test_gaps_says_so_at_the_top_rank():
    # Dues are recorded for the current year, so this holds whatever the date
    # -- paying this year always counts, grace window or not.
    m = MemberSheet(slug="x", display_name="X", waiver=True,
                    veteran_garb=True, professions={"Cook": 2},
                    dues_years={dt.date.today().year: True})
    assert gaps(m) == []
    assert "Nothing left" in views.format_gaps(m)


def test_sheet_omits_unearned_proficiencies():
    m = MemberSheet(slug="x", display_name="X", waiver=True,
                    weapons={"Flail": 2, "Archery": 0},
                    professions={"Cook": 1, "Brewer": 0})
    out = views.format_sheet(m)
    assert "Flail" in out and "Cook" in out
    assert "Archery" not in out and "Brewer" not in out


def test_sheet_handles_a_member_with_nothing():
    m = MemberSheet(slug="x", display_name="X",
                    weapons={"Flail": 0}, professions={"Cook": 0})
    out = views.format_sheet(m)
    assert "No weapon styles yet" in out


# --- roster ----------------------------------------------------------------

def test_roster_splits_on_dues_for_the_year(sheets):
    out = views.format_roster(sheets, 2026)
    assert "Current members (2026)" in out
    assert "Last year's members" in out


def test_roster_explains_an_empty_current_list(sheets):
    """An empty list should say why rather than looking like the club has no
    members at all."""
    out = views.format_roster(sheets, 2099)
    assert "Nobody has 2099 dues recorded yet" in out


def test_roster_matches_the_websites_split(sheets):
    """Bot and site must tell the same story about who is current."""
    year = 2026
    expected = {s.display_name or s.slug for s in sheets if s.paid_for(year)}
    out = views.format_roster(sheets, year)
    current_section = out.split("Last year's members")[0]
    for name in expected:
        assert name in current_section


def test_roster_stays_within_the_message_limit(sheets):
    for year in (2025, 2026, 2027):
        assert len(views.format_roster(sheets, year)) <= views.MESSAGE_LIMIT


# --- lookup failures -------------------------------------------------------

def test_no_match_names_the_query():
    assert "nobody" in views.format_no_match("nobody")


def test_ambiguous_lists_the_candidates():
    a = MemberSheet(slug="kam", display_name="Kam")
    b = MemberSheet(slug="kamber", display_name="Kamber")
    out = views.format_ambiguous("kam", [a, b])
    assert "`kam`" in out and "`kamber`" in out


# --- three roster groups ---------------------------------------------------

def test_a_member_who_never_paid_is_not_a_last_years_member(sheets):
    """Filing them under last year's is wrong about them, and buries a new
    member in a long list of lapsed ones."""
    out = views.format_roster(sheets, 2026)

    assert "**No dues recorded yet** — 1" in out
    lapsed = out.split("Last year's members")[1].split("No dues recorded")[0]
    assert "Nia Newcomer" not in lapsed


def test_the_new_group_is_omitted_when_empty(sheets):
    paid_before = [s for s in sheets if s.dues_years]
    assert "No dues recorded yet" not in views.format_roster(paid_before, 2026)


def test_paying_moves_a_member_out_of_the_new_group(sheets):
    newcomer = next(s for s in sheets if s.slug == "newcomer")
    newcomer.dues_years[2026] = True
    out = views.format_roster(sheets, 2026)
    assert "No dues recorded yet" not in out
    assert "Nia Newcomer" in out.split("Last year's members")[0]


# --- /rank shows what a member can do --------------------------------------

def test_rank_lists_earned_weapon_styles(sheets):
    lamp = next(s for s in sheets if s.slug == "harbinger")
    out = views.format_rank(lamp)
    assert "**Weapon styles**" in out
    assert "Sword & Board (Adept)" in out


def test_rank_lists_earned_professions(sheets):
    lamp = next(s for s in sheets if s.slug == "harbinger")
    assert "**Professions**" in views.format_rank(lamp)


def test_rank_omits_unearned_proficiencies(sheets):
    lamp = next(s for s in sheets if s.slug == "harbinger")
    out = views.format_rank(lamp)
    unearned = [n for n, lvl in lamp.weapons.items() if lvl == 0]
    for name in unearned:
        assert f"{name} (" not in out


def test_proficiency_summary_orders_highest_first():
    line = views.proficiency_summary({"Low": 1, "High": 3, "Mid": 2, "None": 0})
    assert line.index("High") < line.index("Mid") < line.index("Low")
    assert "None" not in line


def test_rank_still_fits_discord_for_every_member(sheets):
    """The added detail must not push a busy member's sheet over the limit."""
    for s in sheets:
        assert len(views.format_rank(s)) <= views.MESSAGE_LIMIT


# --- undoing a dues entry ---------------------------------------------------

def test_dues_removal_is_reported():
    from northernsteppes_bot.views import format_dues_removed
    message = format_dues_removed("Lamp", 2026, removed=True,
                                  still_a_member=True)
    assert "2026" in message and "Lamp" in message
    assert "Peasant" not in message, "no demotion here, so do not mention one"


def test_removing_a_year_that_was_not_there_says_nothing_changed():
    """A slip on the year should read as a no-op, not as a success."""
    from northernsteppes_bot.views import format_dues_removed
    message = format_dues_removed("Lamp", 2025, removed=False,
                                  still_a_member=True)
    assert "nothing changed" in message


def test_losing_the_last_year_warns_about_the_demotion():
    """Rank is gated on having ever paid, so this drops them to Peasant. That
    is too large a consequence to leave to be discovered on the website."""
    from northernsteppes_bot.views import format_dues_removed
    message = format_dues_removed("Lamp", 2026, removed=True,
                                  still_a_member=False)
    assert "Peasant" in message
    assert "only recorded year" in message


# --- awards, weapon styles and professions alike ----------------------------

def test_a_new_award_reads_as_a_gain():
    assert "is now Adept in Cook" in views.format_award("Lamp", "Cook", 0, 2)


def test_re_awarding_the_same_level_says_nothing_changed():
    """Otherwise the awarder wonders whether it took."""
    assert "nothing changed" in views.format_award("Lamp", "Cook", 2, 2)


def test_a_downgrade_is_shown_as_one():
    message = views.format_award("Lamp", "Cook", 3, 1)
    assert "Master" in message and "Proficient" in message


def test_clearing_an_award_reads_as_a_removal():
    """Level 0 is the correction path, and "is now - in Cook" would not say
    that."""
    message = views.format_award("Lamp", "Cook", 2, 0)
    assert "Removed" in message and "Adept" in message


def test_clearing_something_never_awarded_says_nothing_changed():
    assert "nothing changed" in views.format_award("Lamp", "Cook", 0, 0)


# --- the deferred class system ----------------------------------------------

def test_rank_does_not_show_the_class_ladders(sheets):
    """Scout/Soldier/Thief are computed from counters nothing can award, so
    showing them states a rank nobody can act on. Deferred deliberately --
    see DEFERRED.md. This asserts it stays that way, so bringing it back is a
    decision rather than an accident."""
    from northernsteppes_bot.ranks import scout_rank

    qualifies = [s for s in sheets if scout_rank(s) > 0]
    assert qualifies, "expected a fixture that would show a ladder if enabled"

    for sheet in qualifies:
        rendered = views.format_rank(sheet)
        for ladder in ("Scout", "Soldier", "Thief", "Classes"):
            assert ladder not in rendered, (
                f"{ladder} is showing again in /rank for {sheet.slug}"
            )
