"""Tests for command output.

These run against the real member files, because the thing worth checking is
that what a member would actually see is correct and readable. Assertions are
about structure and invariants rather than exact wording, so rephrasing a
message does not break the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from northernsteppes_bot import views
from northernsteppes_bot.ranks import MemberSheet, RANK_NAMES, gaps, rank
from northernsteppes_bot.roster import MemberDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"


@pytest.fixture(scope="module")
def directory() -> MemberDirectory:
    d = MemberDirectory(MEMBERS_DIR)
    d.load()
    return d


@pytest.fixture(scope="module")
def sheets(directory) -> list[MemberSheet]:
    return directory.all()


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
    m = MemberSheet(slug="x", display_name="X", waiver=True, dues=True,
                    veteran_garb=True, professions={"Cook": 2})
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
    out = views.format_roster(sheets, 2025)
    assert "Current members (2025)" in out
    assert "Last year's members" in out


def test_roster_explains_an_empty_current_list(sheets):
    """The live case: nobody has 2026 dues, so the list is empty. It should say
    why rather than looking like the club has no members."""
    out = views.format_roster(sheets, 2026)
    assert "Nobody has 2026 dues recorded yet" in out


def test_roster_matches_the_websites_split(sheets):
    """Bot and site must tell the same story about who is current."""
    year = 2025
    expected = {s.display_name or s.slug for s in sheets if s.paid_for(year)}
    out = views.format_roster(sheets, year)
    current_section = out.split("Last year's members")[0]
    for name in expected:
        assert name in current_section


def test_roster_stays_within_the_message_limit(sheets):
    for year in (2024, 2025, 2026):
        assert len(views.format_roster(sheets, year)) <= views.MESSAGE_LIMIT


# --- lookup failures -------------------------------------------------------

def test_no_match_names_the_query():
    assert "nobody" in views.format_no_match("nobody")


def test_ambiguous_lists_the_candidates():
    a = MemberSheet(slug="kam", display_name="Kam")
    b = MemberSheet(slug="kamber", display_name="Kamber")
    out = views.format_ambiguous("kam", [a, b])
    assert "`kam`" in out and "`kamber`" in out
