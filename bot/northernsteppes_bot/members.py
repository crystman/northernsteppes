"""Read member sheets out of ``content/members/_<slug>.md``.

Parsing uses :mod:`tomlkit` rather than a plain TOML reader because the same
documents get written back by the sync job, and tomlkit round-trips formatting:
key order, alignment and blank lines survive, so a bot commit diffs only the
values it actually changed.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomlkit

from .ranks import MemberSheet

#: Frontmatter is delimited by +++ lines, per Zola's TOML convention.
_FRONTMATTER = re.compile(r"\A\+\+\+\s*\n(.*?)\n\+\+\+\s*\n?(.*)\Z", re.S)

#: Keys inside [extra.classes] that are counters or flags rather than 0-3
#: proficiency levels. Kept here so the sync job and the rank rules agree on
#: how to interpret them.
CLASS_COUNTERS = ("Light_Armor", "Armor")
CLASS_FLAGS = ("Steal_10", "Steal_20", "Steal_30", "Look_Part")


class MemberFileError(ValueError):
    """Raised when a member file cannot be parsed."""


def split_frontmatter(text: str) -> tuple[tomlkit.TOMLDocument, str]:
    """Split a Zola page into its TOML frontmatter and body."""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise MemberFileError("no +++ frontmatter block found")
    return tomlkit.parse(match.group(1)), match.group(2)


def slug_for(path: Path) -> str:
    """``content/members/_lamp.md`` -> ``lamp``."""
    return path.stem.lstrip("_")


def parse_member(path: Path) -> MemberSheet:
    """Load one member file into a :class:`MemberSheet`."""
    doc, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    extra = doc.get("extra", {}) or {}

    dues_years: dict[int, bool] = {}
    for key, value in (extra.get("dues", {}) or {}).items():
        try:
            dues_years[int(key)] = bool(value)
        except (TypeError, ValueError):
            # A non-year key under [extra.dues] is a data error worth
            # surfacing rather than silently dropping.
            raise MemberFileError(
                f"{path.name}: [extra.dues] key {key!r} is not a year"
            ) from None

    def as_int_map(table) -> dict[str, int]:
        return {str(k): int(v) for k, v in (table or {}).items() if _is_intish(v)}

    classes = dict(extra.get("classes", {}) or {})

    return MemberSheet(
        slug=slug_for(path),
        display_name=str(doc.get("title", "") or ""),
        waiver=bool(extra.get("Waiver", False)),
        dues=bool(extra.get("Dues", False)),
        veteran_garb=bool(extra.get("Veteran_Garb", False)),
        weapons=as_int_map(extra.get("weapons")),
        professions=as_int_map(extra.get("professions")),
        # Classes mix ints (levels, counters) with bools (thief flags), so this
        # one is passed through rather than coerced.
        classes={str(k): v for k, v in classes.items()},
        dues_years=dues_years,
    )


def _is_intish(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_all(members_dir: Path) -> list[MemberSheet]:
    """Load every member file in ``content/members/``, sorted by slug.

    ``_index.md`` is the section definition, not a member, and is skipped.
    """
    sheets = [
        parse_member(path)
        for path in sorted(members_dir.glob("_*.md"))
        if path.name != "_index.md"
    ]
    return sorted(sheets, key=lambda s: s.slug)
