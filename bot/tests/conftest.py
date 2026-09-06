"""Shared fixtures.

Tests used to load the real member files, which meant somebody's actual
proficiencies could change an assertion. With those files gone the fixtures
are built here instead: a small cast covering each rank and each dues state,
which is what the tests were really reaching for.
"""

from __future__ import annotations

import os

import pytest

from northernsteppes_bot.ranks import MemberSheet

BASIC = ("Single Sword", "Sword & Board", "Rock", "Javelin")
ALL_STYLES = BASIC + (
    "Dual Wield", "2 Handed Weapon", "Flail", "Dagger", "Polearm", "Spear",
    "Archery",
)

THIS_YEAR = 2026
LAST_YEAR = THIS_YEAR - 1


def make_sheet(slug, name, **overrides) -> MemberSheet:
    base = dict(
        slug=slug,
        display_name=name,
        waiver=True,
        veteran_garb=False,
        units=[],
        weapons={s: 0 for s in ALL_STYLES},
        professions={"Cook": 0, "Armorsmith": 0, "Clothier": 0},
        classes={"Light_Armor": 0, "Armor": 0},
        dues_years={},
    )
    for key, value in overrides.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return MemberSheet(**base)


@pytest.fixture
def sample_sheets() -> list[MemberSheet]:
    """A cast covering every rank and every dues state.

    - harbinger: paid this year, every style, veteran garb
    - savage:    paid this year, the four basic styles
    - peasant:   paid last year only, no styles
    - newcomer:  never paid anything
    """
    return [
        make_sheet(
            "harbinger", "Harriet Harbinger",
            veteran_garb=True,
            weapons={s: 2 for s in ALL_STYLES},
            professions={"Cook": 2},
            classes={"Light_Armor": 3, "Armor": 3},
            dues_years={LAST_YEAR: True, THIS_YEAR: True},
        ),
        make_sheet(
            "savage", "Sam Savage",
            weapons={s: 1 for s in BASIC},
            dues_years={THIS_YEAR: True},
            units=["CoWS"],
        ),
        make_sheet(
            "peasant", "Pat Peasant",
            dues_years={LAST_YEAR: True},
        ),
        make_sheet("newcomer", "Nia Newcomer", waiver=False),
    ]


async def seed(pool, sheets) -> None:
    """Insert sheets into a migrated database."""
    async with pool.acquire() as conn:
        for sheet in sheets:
            member_id = await conn.fetchval(
                "insert into members (slug, display_name, waiver,"
                "                     veteran_garb, units)"
                " values ($1, $2, $3, $4, $5) returning id",
                sheet.slug, sheet.display_name, sheet.waiver,
                sheet.veteran_garb, sheet.units,
            )
            for year, paid in sheet.dues_years.items():
                if paid:
                    await conn.execute(
                        "insert into dues_paid (member_id, year, recorded_by)"
                        " values ($1, $2, 'test')",
                        member_id, year,
                    )
            rows = (
                [("weapon", n, v) for n, v in sheet.weapons.items()]
                + [("profession", n, v) for n, v in sheet.professions.items()]
                + [
                    ("counter" if n in ("Light_Armor", "Armor") else "flag",
                     n, int(bool(v)) if isinstance(v, bool) else v)
                    for n, v in sheet.classes.items()
                ]
            )
            for kind, name, level in rows:
                await conn.execute(
                    "insert into proficiencies (member_id, kind, name, level)"
                    " values ($1, $2, $3, $4)"
                    " on conflict (member_id, kind, name) do nothing",
                    member_id, kind, name, level,
                )


def database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or None


requires_db = pytest.mark.skipif(
    database_url() is None,
    reason="set TEST_DATABASE_URL to run database tests",
)
