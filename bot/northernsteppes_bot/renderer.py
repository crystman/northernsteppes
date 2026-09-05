"""Write member state back into `content/members/_<slug>.md`.

The riskiest code in the bot: it rewrites the records the website is built
from. Two properties keep it safe, both enforced by tests.

**It only touches keys it owns.** Classes, professions and the class
counters/flags are deferred from the database while that system is reworked,
so they are still hand-edited. A renderer that wrote "everything the database
knows" would silently delete them. Instead the managed set is explicit, and
anything outside it -- including the page body and any key added later -- is
passed through untouched.

**Unchanged input produces an unchanged file.** Editing is done with tomlkit
against the existing document, so key order, alignment, spacing and comments
survive and the diff shows only the values that actually changed. A sync that
reformatted all 21 files on its first run would bury every real change in
noise, which would defeat the point of keeping these records in git.
"""

from __future__ import annotations

import tomlkit

from .members import split_frontmatter
from .ranks import MemberSheet

#: Top-level frontmatter keys this renderer maintains.
MANAGED_TOP_LEVEL = ("title",)

#: `[extra]` keys this renderer maintains.
MANAGED_EXTRA = ("Waiver", "Dues", "Veteran_Garb", "Units")

#: `[extra]` tables this renderer maintains in full.
MANAGED_TABLES = ("dues", "weapons")

#: Keys removed on write, with the reason. Deliberately a short explicit list
#: rather than "anything the database does not know": the deferred proficiency
#: tables are also unknown to the database, and must survive.
RETIRED_KEYS = {
    # Race is a retired concept in the group. Confirmed with leadership that
    # the sync should drop it rather than it being removed by hand.
    "Race": "retired concept",
    # Superseded by Units, which is plural because a member can belong to
    # more than one.
    "Unit": "superseded by Units",
}


class RenderError(ValueError):
    """Raised when a member file cannot be rendered safely."""


def render_member(existing_text: str, sheet: MemberSheet) -> str:
    """Return `existing_text` updated to match `sheet`.

    Pure: no database, no filesystem, no network. Returns the input unchanged
    when nothing differs, so callers can compare and skip writing.
    """
    doc, _body = split_frontmatter(existing_text)

    # Everything from the closing delimiter onward is copied verbatim rather
    # than rebuilt. Some member files end at "+++" with no trailing newline
    # and some have a body; reconstructing either would put a spurious diff on
    # files whose content did not change.
    closing = existing_text.index("\n+++", len("+++"))
    tail = existing_text[closing + len("\n+++"):]

    if "extra" not in doc:
        raise RenderError(f"{sheet.slug}: no [extra] table to update")
    extra = doc["extra"]

    if sheet.display_name:
        doc["title"] = sheet.display_name

    extra["Waiver"] = sheet.waiver
    extra["Dues"] = sheet.dues
    extra["Veteran_Garb"] = sheet.veteran_garb

    # Write Units before dropping Unit, so the rename migrates the value
    # rather than deleting it. Only when there is something to write: an empty
    # array on the twenty members who never had a unit would be nineteen
    # pointless lines of diff.
    if sheet.units:
        extra["Units"] = list(sheet.units)
    elif "Units" in extra:
        del extra["Units"]

    for key, _reason in RETIRED_KEYS.items():
        if key in extra:
            del extra[key]

    _sync_table(extra, "dues", {
        str(year): True for year, paid in sorted(sheet.dues_years.items()) if paid
    })
    _sync_table(extra, "weapons", dict(sorted(sheet.weapons.items())))

    # No rstrip here: split_frontmatter leaves any trailing blank line inside
    # the captured frontmatter, and most member files have one. Stripping it
    # would put a spurious one-line diff on every file the sync ever touched.
    return "+++\n" + tomlkit.dumps(doc) + "\n+++" + tail


def _sync_table(extra, name: str, wanted: dict) -> None:
    """Bring one `[extra.<name>]` table in line with `wanted`.

    Existing keys are assigned in place so tomlkit keeps their formatting;
    only genuinely new keys are appended, and keys no longer wanted are
    removed. Rebuilding the table wholesale would reorder it and produce a
    diff touching every line.
    """
    if name not in extra:
        if not wanted:
            return
        extra[name] = tomlkit.table()

    table = extra[name]
    for key, value in wanted.items():
        if key not in table or table[key] != value:
            table[key] = value
    for key in [k for k in table.keys() if k not in wanted]:
        del table[key]


def render_changes(files: dict[str, str], sheets: dict[str, MemberSheet]
                   ) -> dict[str, str]:
    """Render many members, returning only those whose content changed.

    `files` and `sheets` are both keyed by slug. A member with no
    corresponding file is skipped rather than created: creating files is a
    wider permission than editing them, and belongs behind its own decision.
    """
    changed: dict[str, str] = {}
    for slug, sheet in sheets.items():
        existing = files.get(slug)
        if existing is None:
            continue
        rendered = render_member(existing, sheet)
        if rendered != existing:
            changed[slug] = rendered
    return changed
