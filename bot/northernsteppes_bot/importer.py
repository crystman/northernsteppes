"""Bootstrap import: load the committed member files into Postgres.

Git is the source of truth for this first load. The files predate the bot, so
the database is being populated *from* them, not the other way round.

Re-running must be a no-op when nothing has changed, because this runs on
every boot until the sync job exists. Rows are upserted by natural key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import asyncpg

from .members import CLASS_COUNTERS, CLASS_FLAGS, load_all
from .ranks import MemberSheet

#: Marker used in dues_paid.recorded_by for rows that came from the files
#: rather than from a Discord command, so imported data stays distinguishable.
BOOTSTRAP_ACTOR = "bootstrap"


@dataclass
class ImportResult:
    members_inserted: int = 0
    members_updated: int = 0
    dues_rows: int = 0
    proficiency_rows: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.members_inserted or self.members_updated)

    def summary(self) -> str:
        return (
            f"{self.members_inserted} inserted, {self.members_updated} updated, "
            f"{self.dues_rows} dues rows, {self.proficiency_rows} proficiencies"
        )


def classify(kind_hint: str, name: str) -> str:
    """Map a frontmatter key to a proficiencies.kind value."""
    if kind_hint == "class":
        if name in CLASS_COUNTERS:
            return "counter"
        if name in CLASS_FLAGS:
            return "flag"
    return kind_hint


def proficiency_rows(sheet: MemberSheet) -> list[tuple[str, str, int]]:
    """Flatten a sheet's weapons, professions and classes into rows."""
    rows: list[tuple[str, str, int]] = []
    for name, level in sheet.weapons.items():
        rows.append(("weapon", name, int(level)))
    for name, level in sheet.professions.items():
        rows.append(("profession", name, int(level)))
    for name, value in sheet.classes.items():
        kind = classify("class", name)
        # Thief flags are booleans in TOML; store them as 0/1.
        level = int(bool(value)) if kind == "flag" else int(value or 0)
        rows.append((kind, name, level))
    return sorted(rows)


async def import_member(conn: asyncpg.Connection, sheet: MemberSheet) -> str:
    """Upsert one member and their child rows. Returns 'inserted' or 'updated'."""
    row = await conn.fetchrow(
        """
        insert into members (slug, display_name, waiver, veteran_garb)
             values ($1, $2, $3, $4)
        on conflict (slug) do update set
                display_name = excluded.display_name,
                waiver       = excluded.waiver,
                veteran_garb = excluded.veteran_garb,
                updated_at   = now()
          returning id, (xmax = 0) as inserted
        """,
        sheet.slug, sheet.display_name, sheet.waiver, sheet.veteran_garb,
    )
    member_id = row["id"]

    # A row means "paid", so a year recorded as false contributes nothing.
    # None currently are, but the files are hand-edited and could be.
    for year, paid in sorted(sheet.dues_years.items()):
        if not paid:
            continue
        await conn.execute(
            """
            insert into dues_paid (member_id, year, recorded_by)
                 values ($1, $2, $3)
            on conflict (member_id, year) do nothing
            """,
            member_id, year, BOOTSTRAP_ACTOR,
        )

    for kind, name, level in proficiency_rows(sheet):
        await conn.execute(
            """
            insert into proficiencies (member_id, kind, name, level)
                 values ($1, $2, $3, $4)
            on conflict (member_id, kind, name) do update set level = excluded.level
            """,
            member_id, kind, name, level,
        )

    return "inserted" if row["inserted"] else "updated"


async def bootstrap(pool: asyncpg.Pool, members_dir: Path) -> ImportResult:
    """Import every member file. Idempotent."""
    sheets = load_all(members_dir)
    result = ImportResult()

    async with pool.acquire() as conn:
        # One transaction for the whole import: a partial roster is worse than
        # no roster, since commands would answer confidently about half the club.
        async with conn.transaction():
            for sheet in sheets:
                outcome = await import_member(conn, sheet)
                if outcome == "inserted":
                    result.members_inserted += 1
                else:
                    result.members_updated += 1
                result.dues_rows += sum(
                    1 for paid in sheet.dues_years.values() if paid
                )
                result.proficiency_rows += len(proficiency_rows(sheet))

    return result
