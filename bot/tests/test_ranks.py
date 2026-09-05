"""Unit tests for the rank rules.

Fixtures are built by factory function rather than by loading real member
files, so a change to somebody's actual proficiencies can never break these.
Parity against the site's Tera macros is covered separately, in
test_parity.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from northernsteppes_bot.ranks import (
    BASIC_STYLES,
    DUES_BEHIND,
    DUES_NEVER,
    DUES_PAID,
    MemberSheet,
    dues_state,
    has_ever_paid,
    gaps,
    rank,
    rank_name,
    scout_rank,
    soldier_rank,
    thief_rank,
)

ALL_STYLES = (
    "Single Sword",
    "Sword & Board",
    "Dual Wield",
    "2 Handed Weapon",
    "Flail",
    "Dagger",
    "Polearm",
    "Spear",
    "Rock",
    "Javelin",
    "Archery",
)


#: Fixed clock for every test here. Without it the suite would change
#: behaviour on 1 January and again on 1 April, when the dues grace period
#: opens and closes. Mid-June: well outside the grace window.
TODAY = date(2026, 6, 15)


def sheet(**overrides) -> MemberSheet:
    """A member with every style and profession at zero.

    `dues=True` records dues for TODAY's year, since rank now reads the
    per-year table rather than the legacy flag.
    """
    dues = overrides.pop("dues", False)
    overrides.setdefault(
        "dues_years", {TODAY.year: True} if dues else {}
    )
    base = dict(
        slug="test",
        display_name="Test",
        waiver=False,
        dues=dues,
        veteran_garb=False,
        weapons={s: 0 for s in ALL_STYLES},
        professions={"Armorsmith": 0, "Blacksmith": 0, "Cook": 0},
        classes={"Light_Armor": 0, "Armor": 0},
    )
    weapons = {**base["weapons"], **overrides.pop("weapons", {})}
    professions = {**base["professions"], **overrides.pop("professions", {})}
    classes = {**base["classes"], **overrides.pop("classes", {})}
    return MemberSheet(**{**base, **overrides,
                          "weapons": weapons,
                          "professions": professions,
                          "classes": classes})


# --- overall rank ----------------------------------------------------------

def test_no_waiver_is_unranked():
    assert rank(sheet()) == 0
    assert rank_name(sheet()) == "Unranked"


def test_waiver_alone_is_peasant():
    assert rank_name(sheet(waiver=True)) == "Peasant"


def test_dues_without_proficiency_stays_peasant():
    assert rank_name(sheet(waiver=True, dues=True)) == "Peasant"


def test_basic_four_styles_reach_savage():
    m = sheet(waiver=True, dues=True, weapons={s: 1 for s in BASIC_STYLES})
    assert rank_name(m) == "Savage"


def test_one_missing_basic_style_does_not_reach_savage():
    styles = {s: 1 for s in BASIC_STYLES}
    del styles["Rock"]
    m = sheet(waiver=True, dues=True, weapons=styles)
    assert rank_name(m) == "Peasant"


def test_single_proficient_profession_reaches_savage():
    m = sheet(waiver=True, dues=True, professions={"Cook": 1})
    assert rank_name(m) == "Savage"


def test_veteran_garb_plus_adept_profession_reaches_harbinger():
    m = sheet(
        waiver=True,
        dues=True,
        veteran_garb=True,
        weapons={s: 1 for s in BASIC_STYLES},
        professions={"Cook": 2},
    )
    assert rank_name(m) == "Harbinger"


def test_veteran_garb_plus_every_style_reaches_harbinger():
    m = sheet(
        waiver=True,
        dues=True,
        veteran_garb=True,
        weapons={s: 1 for s in ALL_STYLES},
    )
    assert rank_name(m) == "Harbinger"


def test_one_unproficient_style_blocks_harbinger():
    weapons = {s: 1 for s in ALL_STYLES}
    weapons["Archery"] = 0
    m = sheet(waiver=True, dues=True, veteran_garb=True, weapons=weapons)
    assert rank_name(m) == "Savage"


def test_veteran_garb_without_dues_is_still_peasant():
    m = sheet(waiver=True, veteran_garb=True, weapons={s: 3 for s in ALL_STYLES})
    assert rank_name(m) == "Peasant"


def test_professions_route_reaches_harbinger():
    """The non-combat route: dues, a second-rank profession and veteran garb,
    with no combat styles at all."""
    m = sheet(waiver=True, dues=True, veteran_garb=True, professions={"Cook": 2})
    assert rank_name(m) == "Harbinger"


def test_professions_route_needs_second_rank_for_harbinger():
    """One proficient profession is Savage; it takes a second rank to advance."""
    m = sheet(waiver=True, dues=True, veteran_garb=True, professions={"Cook": 1})
    assert rank_name(m) == "Savage"


def test_professions_route_needs_garb_for_harbinger():
    m = sheet(waiver=True, dues=True, veteran_garb=False, professions={"Cook": 3})
    assert rank_name(m) == "Savage"


def test_professions_route_needs_dues():
    """Both non-combat ranks sit behind dues being paid."""
    m = sheet(waiver=True, dues=False, veteran_garb=True, professions={"Cook": 3})
    assert rank_name(m) == "Peasant"


# --- class ladders ---------------------------------------------------------

def test_scout_needs_two_adept_styles():
    m = sheet(waiver=True, dues=True, weapons={**{s: 1 for s in BASIC_STYLES},
                                               "Single Sword": 2, "Rock": 2})
    assert scout_rank(m) == 1


def test_scout_ladder_blocked_below_savage():
    m = sheet(waiver=True, weapons={s: 3 for s in ALL_STYLES})
    assert scout_rank(m) == 0


def test_soldier_ignores_ranged_for_recruit():
    """Javelin and Archery do not count toward the Recruit melee threshold."""
    m = sheet(
        waiver=True,
        dues=True,
        weapons={**{s: 1 for s in BASIC_STYLES}, "Javelin": 2, "Archery": 2},
    )
    assert soldier_rank(m) == 0


def test_soldier_recruit_on_two_adept_melee():
    m = sheet(
        waiver=True,
        dues=True,
        weapons={**{s: 1 for s in BASIC_STYLES},
                 "Single Sword": 2, "Sword & Board": 2},
    )
    assert soldier_rank(m) == 1


def test_thief_footpad_requires_steal_10():
    m = sheet(waiver=True, dues=True, weapons={s: 1 for s in BASIC_STYLES},
              classes={"Steal_10": True})
    assert thief_rank(m) == 1


def test_thief_master_requires_look_part():
    m = sheet(
        waiver=True, dues=True, veteran_garb=True,
        weapons={s: 1 for s in ALL_STYLES},
        classes={"Steal_10": True, "Steal_20": True,
                 "Steal_30": True, "Look_Part": False},
    )
    assert thief_rank(m) == 2


# --- gaps ------------------------------------------------------------------

def test_gaps_unranked_asks_for_waiver():
    assert gaps(sheet()) == ["Sign a waiver"]


def test_gaps_peasant_asks_for_dues():
    assert "Pay membership dues" in gaps(sheet(waiver=True))


def test_gaps_savage_asks_for_veteran_garb():
    m = sheet(waiver=True, dues=True, weapons={s: 1 for s in BASIC_STYLES})
    assert "Own veteran level garb" in gaps(m)


def test_gaps_empty_at_harbinger():
    m = sheet(waiver=True, dues=True, veteran_garb=True,
              weapons={s: 1 for s in ALL_STYLES})
    assert gaps(m) == []


def test_gaps_names_the_missing_styles():
    m = sheet(waiver=True, dues=True, weapons={"Single Sword": 1, "Rock": 1})
    text = " ".join(gaps(m))
    assert "Sword & Board" in text and "Javelin" in text


def test_gaps_offers_the_non_combat_route_to_harbinger():
    """A Savage who got there through professions must not be told the only
    way up is the four combat styles -- the non-combat route reaches
    Harbinger too."""
    m = sheet(waiver=True, dues=True, veteran_garb=True,
              professions={"Cook": 1})
    text = " ".join(gaps(m))
    assert "non-combat profession" in text


def test_gaps_offers_both_routes_to_a_combat_savage():
    m = sheet(waiver=True, dues=True, veteran_garb=True,
              weapons={s: 1 for s in BASIC_STYLES})
    text = " ".join(gaps(m))
    assert "non-combat profession" in text
    assert "Archery" in text, "should name the styles still unproficient"


# --- dues states ----------------------------------------------------------

def paid(*years) -> MemberSheet:
    """A member who would be Savage, if their dues count."""
    m = sheet(waiver=True, weapons={s: 1 for s in BASIC_STYLES})
    m.dues_years = {y: True for y in years}
    return m


def test_current_year_dues_show_as_paid():
    assert dues_state(paid(2026), date(2026, 6, 15)) == DUES_PAID


def test_old_dues_show_as_behind():
    assert dues_state(paid(2024), date(2026, 6, 15)) == DUES_BEHIND


def test_no_dues_at_all_shows_as_never():
    assert dues_state(paid(), date(2026, 6, 15)) == DUES_NEVER


def test_current_year_wins_over_older_years():
    assert dues_state(paid(2024, 2026), date(2026, 6, 15)) == DUES_PAID


def test_a_year_recorded_false_does_not_count():
    m = paid()
    m.dues_years = {2025: False}
    assert has_ever_paid(m) is False
    assert dues_state(m, date(2026, 6, 15)) == DUES_NEVER


# --- what dues gate ---------------------------------------------------------

def test_being_behind_does_not_cost_a_rank():
    """The point: a lapsed member has not un-earned their proficiencies."""
    m = paid(2024)
    assert dues_state(m, date(2026, 6, 15)) == DUES_BEHIND
    assert rank_name(m) == "Savage"


def test_never_paying_caps_at_peasant():
    m = paid()
    assert rank_name(m) == "Peasant"


def test_rank_does_not_change_with_the_date():
    """Rank is deliberately clock-independent now, so nobody is demoted by the
    calendar rolling over."""
    m = paid(2024)
    assert rank(m) == 2


def test_gaps_asks_for_dues_only_when_never_paid():
    assert "Pay membership dues" in gaps(paid())
    assert "Pay membership dues" not in gaps(paid(2024))
