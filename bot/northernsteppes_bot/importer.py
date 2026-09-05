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

from .members import load_all
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


def proficiency_rows(sheet: MemberSheet) -> list[tuple[str, str, int]]:
    """Flatten a sheet's weapon styles into rows.

    Weapons only. Classes, professions and the class counters/flags are being
    reworked, so proficiency_defs defines none of them and the foreign key
    would reject them. Leaving them out of the database entirely means there
    is nothing to clean up when the rework lands.

    They are still parsed from the files and still drive rank(), which reads
    from a MemberSheet rather than from the database -- see the note in
    DESIGN.md about where the read commands source them from.
    """
    return sorted(
        ("weapon", name, int(level)) for name, level in sheet.weapons.items()
    )


async def import_member(conn: asyncpg.Connection, sheet: MemberSheet) -> str:
    """Upsert one member and their child rows. Returns 'inserted' or 'updated'."""
    row = await conn.fetchrow(
        """
        insert into members (slug, display_name, waiver, veteran_garb, units)
             values ($1, $2, $3, $4, $5)
        on conflict (slug) do update set
                display_name = excluded.display_name,
                waiver       = excluded.waiver,
                veteran_garb = excluded.veteran_garb,
                units        = excluded.units,
                updated_at   = now()
          returning id, (xmax = 0) as inserted
        """,
        sheet.slug, sheet.display_name, sheet.waiver, sheet.veteran_garb,
        sheet.units,
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
    """Import every member file from a directory. Idempotent."""
    return await bootstrap_sheets(pool, load_all(members_dir))


async def bootstrap_sheets(pool: asyncpg.Pool,
                           sheets: list[MemberSheet]) -> ImportResult:
    """Import already-parsed sheets. Idempotent.

    Split from bootstrap so the bot can import what it loaded, whatever the
    source. On a host that builds only bot/ there is no members directory to
    point at, and the sheets came over the network.
    """
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
