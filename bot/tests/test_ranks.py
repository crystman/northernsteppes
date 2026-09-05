"""Unit tests for the rank rules.

Fixtures are built by factory function rather than by loading real member
files, so a change to somebody's actual proficiencies can never break these.
Parity against the site's Tera macros is covered separately, in
test_parity.py.
"""

from __future__ import annotations

import pytest

from northernsteppes_bot.ranks import (
    BASIC_STYLES,
    MemberSheet,
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


def sheet(**overrides) -> MemberSheet:
    """A member with every style and profession at zero."""
    base = dict(
        slug="test",
        display_name="Test",
        waiver=False,
        dues=False,
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
