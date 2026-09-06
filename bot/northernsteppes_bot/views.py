"""Command responses, as pure functions.

Every command's output is built here rather than inside its handler, so it can
be tested without a Discord connection, a token or a guild. The handlers in
bot.py stay thin enough to be obviously correct by inspection.

All text is plain Markdown. Discord renders it, but nothing here depends on
discord.py -- these are strings.
"""

from __future__ import annotations

from .ranks import (
    DUES_BEHIND,
    DUES_PAID,
    LEVEL_NAMES,
    RANK_NAMES,
    MemberSheet,
    dues_state,
    has_ever_paid,
    gaps,
    rank,
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


def describe_dues(sheet: MemberSheet, today=None) -> str:
    """One line describing dues, distinguishing "behind" from "never paid".

    Three states rather than two. The old display read the `Dues` flag, which
    is true for every member regardless of what they have actually paid, so
    everyone showed an identical tick while /roster reported nobody as current.
    The warning says "you are a member, but not up to date" without implying
    either extreme.
    """
    import datetime as _dt
    year = (today or _dt.date.today()).year
    state = dues_state(sheet, today)
    if state == DUES_PAID:
        return f"✅ Dues paid for {year}"
    if state == DUES_BEHIND:
        return f"⚠️ Dues not up to date — nothing recorded for {year}"
    return "❌ No dues ever recorded"


def proficiency_summary(levels: dict) -> str:
    """Earned proficiencies, highest first, on one line.

    Compact because /rank already carries the rank, the reasons and the
    ladders, and Discord cuts a message off at 2000 characters.
    """
    earned = sorted(
        ((name, level) for name, level in levels.items() if level > 0),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return " · ".join(f"{name} ({LEVEL_NAMES[level]})" for name, level in earned)


def _name(sheet: MemberSheet) -> str:
    return sheet.display_name or sheet.slug


def format_rank(sheet: MemberSheet) -> str:
    """`/rank` -- the rank, why it is that, and the class ladders."""
    r = rank(sheet)
    lines = [f"**{_name(sheet)}** — {RANK_NAMES[r]}", ""]

    lines.append("**Why**")
    lines.append(f"{'✅' if sheet.waiver else '❌'} Waiver on file")
    lines.append(describe_dues(sheet))
    lines.append(f"{'✅' if sheet.veteran_garb else '❌'} Veteran garb")

    # The Scout/Soldier/Thief ladders are deliberately not shown. That system
    # is being reworked, and the counters they are computed from cannot be
    # awarded, so the answer would be one nobody can act on. See DEFERRED.md.

    # What they can do, not only what they lack. Asking after somebody's rank
    # is usually asking what they fight with, and the answer was missing --
    # only /me showed it, and only for yourself.
    styles = proficiency_summary(sheet.weapons)
    if styles:
        lines += ["", "**Weapon styles**", styles]
    professions = proficiency_summary(sheet.professions)
    if professions:
        lines += ["", "**Professions**", professions]

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
    """`/roster` -- current members by rank, then those behind, then new.

    Three groups, not two. Splitting only on "paid this year" filed a member
    who had never paid at all under last year's members, which is wrong about
    them and hides a new member in a long list of lapsed ones.
    """
    current, behind, new = [], [], []
    for sheet in sheets:
        if sheet.paid_for(year):
            current.append(sheet)
        elif has_ever_paid(sheet):
            behind.append(sheet)
        else:
            new.append(sheet)

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

    if behind:
        lines += ["", f"**Last year's members** — {len(behind)}"]
        lines.append(", ".join(sorted(_name(s) for s in behind)))

    if new:
        lines += ["", f"**No dues recorded yet** — {len(new)}"]
        lines.append(", ".join(sorted(_name(s) for s in new)))

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


def format_link_dangling(slug: str) -> str:
    """The link points at a member record that no longer exists."""
    return (
        f"⚠️ Your Discord account is linked to `{slug}`, which no longer has "
        "a member record. Ask leadership to re-link you."
    )


def format_unlinked(year: int) -> str:
    return (
        "Your Discord account isn't linked to a member record yet, so I don't "
        "know whose sheet to show. Ask leadership to link you, or use "
        "`/rank <name>`."
    )


# --- write command responses ----------------------------------------------

def format_writes_disabled(config) -> str:
    """Why a write command refused. Says which piece is missing, because
    "you can't do that" with no reason is the most annoying possible reply."""
    if config.leadership_role_id is None:
        return (
            "⚙️ Write commands are switched off: no leadership role is "
            "configured yet, so I can't tell who is allowed to use them."
        )
    return (
        "⚙️ Write commands are switched off: no database is configured, so "
        "there is nowhere to record this."
    )


def format_wrong_guild() -> str:
    """Refuse a write from a server this bot is not configured for.

    Role ids are unique per guild, so a leadership role elsewhere would not
    have matched anyway -- but relying on that is implicit. Saying no
    explicitly means a test deployment invited to the real server cannot edit
    real records by accident.
    """
    return (
        "🚫 This bot instance is configured for a different server, so it "
        "will not change records from here."
    )


def format_not_leadership() -> str:
    return "🔒 Only leadership can use that command."


def format_member_not_in_database(name: str) -> str:
    """The member exists in the files but has no database row.

    Distinct from "no member matches": that one means the name was wrong,
    this one means the name was right and the database is behind. Reporting
    the first for the second sent somebody hunting for a typo that was not
    there.
    """
    return (
        f"⚠️ **{name}** is on the roster but has no record in the database "
        "yet, so there is nothing to update. This usually clears itself on "
        "the next restart; if it does not, tell whoever runs the bot."
    )


def format_dues_recorded(name: str, year: int, already: bool) -> str:
    if already:
        return f"ℹ️ {name} already had {year} dues recorded — nothing changed."
    return f"✅ Recorded {year} dues for **{name}**."


def format_dues_removed(name: str, year: int, removed: bool,
                        still_a_member: bool) -> str:
    """Report a dues removal, and say when it costs a member their rank.

    Losing the last recorded year drops somebody to Peasant, because rank is
    gated on having ever paid. That is a large consequence for a one-word
    command, so it is stated rather than left to be discovered on the website.
    """
    if not removed:
        return f"ℹ️ {name} had no {year} dues recorded — nothing changed."
    if still_a_member:
        return f"✅ Removed {year} dues for **{name}**."
    return (
        f"✅ Removed {year} dues for **{name}**.\n"
        "⚠️ That was their only recorded year, so they now read as "
        "never having paid and drop to Peasant."
    )


def format_award(name: str, what: str, previous, level: int) -> str:
    """Report an award, including when it is a downgrade or a no-op.

    Saying "set to Adept" when it was already Adept invites the awarder to
    wonder whether it took.

    ``what`` is a weapon style or a profession -- both run 0-3 on the same
    LEVEL_NAMES scale, so both read correctly from one function.
    """
    new = LEVEL_NAMES[level]
    if previous is None or previous == 0:
        if level == 0:
            return f"ℹ️ **{name}** had no {what} to remove — nothing changed."
        return f"✅ **{name}** is now {new} in {what}."
    if previous == level:
        return f"ℹ️ **{name}** was already {new} in {what} — nothing changed."
    if level == 0:
        return f"✅ Removed {what} from **{name}** (was {LEVEL_NAMES[previous]})."
    old = LEVEL_NAMES[previous]
    arrow = "↑" if level > previous else "↓"
    return f"✅ **{name}**: {what} {old} {arrow} {new}."


def format_flag_set(name: str, label: str, value: bool) -> str:
    return f"✅ **{name}** — {label} set to {'yes' if value else 'no'}."


def format_linked(name: str, mention: str, taken_from: str | None) -> str:
    if taken_from:
        return (
            f"✅ Linked {mention} to **{name}**.\n"
            f"⚠️ That account was previously linked to `{taken_from}`, which "
            "is now unlinked."
        )
    return f"✅ Linked {mention} to **{name}**."


def format_unknown_proficiency(name: str, known: list[str]) -> str:
    listing = ", ".join(f"`{k}`" for k in known)
    return f"❌ **{name}** is not a known weapon style. Known: {listing}"


def format_member_added(display_name: str, slug: str) -> str:
    return (
        f"✅ Added **{display_name}** as `{slug}`.\n"
        "Their page appears on the website once the sync runs."
    )


def format_duplicate_member(slug: str) -> str:
    return (
        f"❌ A member with slug `{slug}` already exists. "
        "Pass a different `slug` if this is a different person."
    )


def format_invalid_slug(slug: str) -> str:
    return (
        f"❌ `{slug}` is not a usable slug. Slugs appear in member URLs, "
        "so they must be lowercase letters and digits separated by dashes."
    )
