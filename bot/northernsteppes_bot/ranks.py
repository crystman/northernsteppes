"""Rank and class-ladder rules for Northern Steppes members.

This is a deliberate second implementation of the logic in
``templates/ranks.html``. The site keeps its Tera macros so it stays
self-contained and never depends on the bot; the bot needs the same rules to
answer ``/rank`` and ``/gaps`` without a site build.

Two implementations means drift risk, so ``tests/test_parity.py`` builds the
site and asserts the two agree for every member. Any divergence fails CI.

Every function here is pure: it takes a MemberSheet and returns a value. No
database, no network, no Discord.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RANK_NAMES = ("Unranked", "Peasant", "Savage", "Harbinger")
LEVEL_NAMES = ("-", "Proficient", "Adept", "Master")

SCOUT_NAMES = ("-", "Novice", "Trailrunner", "Master Scout")
SOLDIER_NAMES = ("-", "Recruit", "Foot Soldier", "Cavalier")
THIEF_NAMES = ("-", "Footpad", "Highwayman", "Master Thief")

#: The four styles that gate Savage via the combat route.
BASIC_STYLES = ("Single Sword", "Sword & Board", "Rock", "Javelin")

#: Styles the Soldier ladder does not count as melee.
RANGED_STYLES = ("Javelin", "Archery")


@dataclass
class MemberSheet:
    """A member's frontmatter, normalised.

    Mirrors the ``[extra]`` tables in ``content/members/_<slug>.md``.
    """

    slug: str
    display_name: str = ""
    waiver: bool = False
    dues: bool = False
    veteran_garb: bool = False
    weapons: dict[str, int] = field(default_factory=dict)
    professions: dict[str, int] = field(default_factory=dict)
    classes: dict[str, int] = field(default_factory=dict)
    dues_years: dict[int, bool] = field(default_factory=dict)

    def weapon(self, name: str) -> int:
        return int(self.weapons.get(name, 0) or 0)

    def class_value(self, name: str) -> int:
        return int(self.classes.get(name, 0) or 0)

    def paid_for(self, year: int) -> bool:
        return bool(self.dues_years.get(year, False))


def has_basic_styles(sheet: MemberSheet) -> bool:
    """True when all four Savage-gating styles are above zero."""
    return all(sheet.weapon(style) > 0 for style in BASIC_STYLES)


def rank(sheet: MemberSheet) -> int:
    """Overall rank, 0-3, indexing into :data:`RANK_NAMES`.

    Port of the ``rank()`` macro in ``templates/ranks.html``. The control flow
    is kept deliberately close to the original so the two can be compared by
    eye, even where it would read better restructured.
    """
    if not sheet.waiver:
        return 0

    # Peasant: waiver on file.
    result = 1
    if not sheet.dues:
        return result

    if has_basic_styles(sheet):
        # Savage via the combat route.
        result = 2
        if sheet.veteran_garb:
            # Harbinger via a second-rank profession...
            if any(v >= 2 for v in sheet.professions.values()):
                result = 3
            else:
                # ...or via proficiency in every combat style.
                result = 3
                for value in sheet.weapons.values():
                    if value == 0:
                        result = 2
    else:
        # Savage via a single proficient non-combat profession.
        for value in sheet.professions.values():
            if value >= 1:
                result = 2

        # Harbinger via the same route: a second-rank non-combat profession
        # plus veteran garb. No combat styles required, matching the rules in
        # content/proficiencies/index.md.
        if result == 2 and sheet.veteran_garb:
            if any(v >= 2 for v in sheet.professions.values()):
                result = 3

    return result


def rank_name(sheet: MemberSheet) -> str:
    return RANK_NAMES[rank(sheet)]


def scout_rank(sheet: MemberSheet, overall: int | None = None) -> int:
    """Scout ladder, 0-3. Port of the ``scout()`` macro."""
    overall = rank(sheet) if overall is None else overall
    i = 0
    if overall > 1 and sum(1 for v in sheet.weapons.values() if v > 1) >= 2:
        i = 1
    if i == 1 and overall > 2 and sheet.class_value("Light_Armor") >= 3:
        if sum(1 for v in sheet.weapons.values() if v > 1) >= 4:
            i = 2
    if i == 2 and sheet.class_value("Light_Armor") >= 6:
        if sheet.weapon("Javelin") >= 3 or sheet.weapon("Archery") >= 3:
            i = 3
    return i


def soldier_rank(sheet: MemberSheet, overall: int | None = None) -> int:
    """Soldier ladder, 0-3. Port of the ``soldier()`` macro."""
    overall = rank(sheet) if overall is None else overall
    melee_over_1 = sum(
        1 for w, v in sheet.weapons.items() if v > 1 and w not in RANGED_STYLES
    )
    ranged_over_1 = sum(
        1 for w, v in sheet.weapons.items() if v > 1 and w in RANGED_STYLES
    )

    i = 0
    if overall > 1 and melee_over_1 >= 2:
        i = 1
    if i == 1 and overall > 2 and sheet.class_value("Armor") >= 3:
        if melee_over_1 >= 3 and ranged_over_1 >= 1:
            i = 2
    if i == 2 and sheet.class_value("Armor") >= 6:
        if any(v >= 3 for v in sheet.weapons.values()):
            i = 3
    return i


def thief_rank(sheet: MemberSheet, overall: int | None = None) -> int:
    """Thief ladder, 0-3. Port of the ``thief()`` macro."""
    overall = rank(sheet) if overall is None else overall
    i = 0
    if overall > 1 and sheet.classes.get("Steal_10"):
        i = 1
    if i == 1 and overall > 2 and sheet.classes.get("Steal_20"):
        i = 2
    if i == 2 and sheet.classes.get("Steal_30") and sheet.classes.get("Look_Part"):
        i = 3
    return i


def gaps(sheet: MemberSheet) -> list[str]:
    """What this member still needs in order to reach the next rank.

    Returns an empty list at Harbinger. This is the capability the site does
    not have: the rules are precise enough to say exactly what is missing,
    which today is a conversation with leadership.
    """
    current = rank(sheet)

    if current == 0:
        return ["Sign a waiver"]

    if current == 1:
        needs: list[str] = []
        if not sheet.dues:
            needs.append("Pay membership dues")
        missing = [s for s in BASIC_STYLES if sheet.weapon(s) == 0]
        if missing and not any(v >= 1 for v in sheet.professions.values()):
            needs.append(
                "Become proficient in one non-combat profession, or in: "
                + ", ".join(missing)
            )
        return needs

    if current == 2:
        needs = []
        if not sheet.veteran_garb:
            needs.append("Own veteran level garb")
        if not has_basic_styles(sheet):
            # See the note in rank(): Harbinger is unreachable from the
            # professions route as currently implemented.
            missing = [s for s in BASIC_STYLES if sheet.weapon(s) == 0]
            needs.append("Become proficient in: " + ", ".join(missing))
            return needs
        if not any(v >= 2 for v in sheet.professions.values()):
            unproficient = sorted(w for w, v in sheet.weapons.items() if v == 0)
            if unproficient:
                needs.append(
                    "Reach Adept in a non-combat profession, or become "
                    "proficient in: " + ", ".join(unproficient)
                )
        return needs

    return []
