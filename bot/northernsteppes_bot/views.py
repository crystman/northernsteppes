"""Command responses, as pure functions.

Every command's output is built here rather than inside its handler, so it can
be tested without a Discord connection, a token or a guild. The handlers in
bot.py stay thin enough to be obviously correct by inspection.

All text is plain Markdown. Discord renders it, but nothing here depends on
discord.py -- these are strings.
"""

from __future__ import annotations

from .ranks import (
    LEVEL_NAMES,
    RANK_NAMES,
    SCOUT_NAMES,
    SOLDIER_NAMES,
    THIEF_NAMES,
    MemberSheet,
    gaps,
    rank,
    scout_rank,
    soldier_rank,
    thief_rank,
)

#: Discord rejects messages over 2000 characters.
MESSAGE_LIMIT = 2000


def truncate(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """Trim to Discord's limit on a line boundary, saying what was cut.

    Silently losing the tail of a roster would be worse than saying so: the
    reader cannot tell a short list from a truncated one.
    """
    if len(text) <= limit:
        return text
    notice = "\n… truncated"
    keep = limit - len(notice)
    cut = text[:keep]
    if "\n" in cut:
        cut = cut[: cut.rindex("\n")]
    return cut + notice


def _name(sheet: MemberSheet) -> str:
    return sheet.display_name or sheet.slug


def format_rank(sheet: MemberSheet) -> str:
    """`/rank` -- the rank, why it is that, and the class ladders."""
    r = rank(sheet)
    lines = [f"**{_name(sheet)}** — {RANK_NAMES[r]}", ""]

    lines.append("**Why**")
    lines.append(f"{'✅' if sheet.waiver else '❌'} Waiver on file")
    lines.append(f"{'✅' if sheet.dues else '❌'} Dues paid")
    lines.append(
        f"{'✅' if sheet.veteran_garb else '❌'} Veteran garb"
    )

    ladders = [
        ("Scout", SCOUT_NAMES[scout_rank(sheet, r)]),
        ("Soldier", SOLDIER_NAMES[soldier_rank(sheet, r)]),
        ("Thief", THIEF_NAMES[thief_rank(sheet, r)]),
    ]
    earned = [f"{label}: {value}" for label, value in ladders if value != "-"]
    if earned:
        lines += ["", "**Classes**", " · ".join(earned)]

    remaining = gaps(sheet)
    if remaining:
        lines += ["", "**Next rank needs**"]
        lines += [f"• {g}" for g in remaining]

    return truncate("\n".join(lines))


def format_gaps(sheet: MemberSheet) -> str:
    """`/gaps` -- what is still missing, and nothing else."""
    remaining = gaps(sheet)
    r = rank(sheet)
    if not remaining:
        return truncate(
            f"**{_name(sheet)}** is {RANK_NAMES[r]} — the highest rank. "
            "Nothing left to earn."
        )
    lines = [
        f"**{_name(sheet)}** is {RANK_NAMES[r]}. To reach {RANK_NAMES[r + 1]}:",
        "",
    ]
    lines += [f"• {g}" for g in remaining]
    return truncate("\n".join(lines))


def format_sheet(sheet: MemberSheet) -> str:
    """`/me` -- the full proficiency sheet."""
    r = rank(sheet)
    lines = [f"**{_name(sheet)}** — {RANK_NAMES[r]}", ""]

    styles = [
        (name, level) for name, level in sorted(sheet.weapons.items()) if level > 0
    ]
    if styles:
        lines.append("**Weapon styles**")
        lines += [f"• {n} — {LEVEL_NAMES[l]}" for n, l in styles]
    else:
        lines.append("*No weapon styles yet.*")

    professions = [
        (name, level)
        for name, level in sorted(sheet.professions.items())
        if level > 0
    ]
    if professions:
        lines += ["", "**Professions**"]
        lines += [f"• {n} — {LEVEL_NAMES[l]}" for n, l in professions]

    remaining = gaps(sheet)
    if remaining:
        lines += ["", "**Next rank needs**"]
        lines += [f"• {g}" for g in remaining]

    return truncate("\n".join(lines))


def format_roster(sheets: list[MemberSheet], year: int) -> str:
    """`/roster` -- current members by rank, then everyone else.

    Mirrors the website's split so the two cannot tell different stories: a
    member is current if their dues are recorded for `year`.
    """
    current = [s for s in sheets if s.paid_for(year)]
    lapsed = [s for s in sheets if not s.paid_for(year)]

    lines = [f"**Current members ({year})** — {len(current)}"]
    if current:
        by_rank: dict[int, list[MemberSheet]] = {}
        for s in current:
            by_rank.setdefault(rank(s), []).append(s)
        for r in sorted(by_rank, reverse=True):
            names = ", ".join(sorted(_name(s) for s in by_rank[r]))
            lines.append(f"**{RANK_NAMES[r]}** — {names}")
    else:
        lines.append(
            f"*Nobody has {year} dues recorded yet, so this list is empty. "
            "It fills as dues come in.*"
        )

    if lapsed:
        lines += ["", f"**Last year's members** — {len(lapsed)}"]
        lines.append(", ".join(sorted(_name(s) for s in lapsed)))

    return truncate("\n".join(lines))


def format_no_match(query: str) -> str:
    return f"No member matches **{query}**. Try `/roster` to see the names."


def format_ambiguous(query: str, matches: list[MemberSheet]) -> str:
    """Several members matched -- ask rather than pick.

    Guessing here is how a proficiency gets awarded to the wrong person.
    """
    names = ", ".join(sorted(f"`{s.slug}`" for s in matches))
    return (
        f"**{query}** matches {len(matches)} members: {names}\n"
        "Use the exact name to pick one."
    )


def format_unlinked(year: int) -> str:
    return (
        "Your Discord account isn't linked to a member record yet, so I don't "
        "know whose sheet to show. Ask leadership to link you, or use "
        "`/rank <name>`."
    )
