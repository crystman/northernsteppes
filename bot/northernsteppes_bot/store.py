"""Database reads and writes for member state.

Writes land here rather than in the repository. The sync job renders these
rows back into `content/members/` and commits them; until that exists, changes
live in Postgres and are visible through the bot but not yet on the website.

Reads overlay the database onto the sheets parsed from the member files. The
database owns identity, waiver, veteran garb, units, dues and weapon styles;
the files still own professions and the class counters/flags, which are
deferred while that system is reworked. Neither source is complete on its own,
so `overlay` merges them and every command reads the result. When the rework
lands and those tables move into the database, this collapses to a plain query.

Every write marks sync_state dirty so the eventual sync job knows the rendered
files are behind.
"""

from __future__ import annotations

from dataclasses import replace

import asyncpg

from .ranks import MemberSheet

#: Frontmatter flags the database owns, mapped to their column.
FLAG_COLUMNS = {
    "waiver": "waiver",
    "veteran_garb": "veteran_garb",
}


class StoreError(RuntimeError):
    """A write could not be applied."""


class UnknownMember(StoreError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"no member with slug {slug!r}")
        self.slug = slug


class UnknownProficiency(StoreError):
    def __init__(self, kind: str, name: str) -> None:
        super().__init__(f"{name!r} is not a known {kind}")
        self.kind, self.name = kind, name


class MemberStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # --- reads -------------------------------------------------------------

    async def overlay(self, sheets: list[MemberSheet]) -> list[MemberSheet]:
        """Return `sheets` with database state applied where it exists.

        A member present in the files but not the database is passed through
        unchanged rather than dropped -- the bootstrap import may not have run,
        and answering "no such member" about somebody who is plainly on the
        website would be worse than answering from slightly stale data.
        """
        async with self.pool.acquire() as conn:
            members = {
                r["slug"]: r for r in await conn.fetch(
                    "select id, slug, display_name, waiver, veteran_garb, units,"
                    "       discord_user_id"
                    "  from members where archived = false"
                )
            }
            if not members:
                return sheets

            dues: dict[str, dict[int, bool]] = {}
            for r in await conn.fetch(
                "select m.slug, d.year from dues_paid d"
                "  join members m on m.id = d.member_id"
            ):
                dues.setdefault(r["slug"], {})[r["year"]] = True

            weapons: dict[str, dict[str, int]] = {}
            for r in await conn.fetch(
                "select m.slug, p.name, p.level from proficiencies p"
                "  join members m on m.id = p.member_id"
                " where p.kind = 'weapon'"
            ):
                weapons.setdefault(r["slug"], {})[r["name"]] = r["level"]

        merged = []
        for sheet in sheets:
            row = members.get(sheet.slug)
            if row is None:
                merged.append(sheet)
                continue
            merged.append(replace(
                sheet,
                display_name=row["display_name"] or sheet.display_name,
                waiver=row["waiver"],
                veteran_garb=row["veteran_garb"],
                units=list(row["units"] or []),
                dues_years=dues.get(sheet.slug, {}),
                # Professions and classes deliberately keep their file values.
                weapons=weapons.get(sheet.slug) or sheet.weapons,
            ))
        return merged

    async def slug_for_discord(self, discord_user_id: int) -> str | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "select slug from members where discord_user_id = $1",
                str(discord_user_id),
            )

    # --- writes ------------------------------------------------------------

    async def _member_id(self, conn: asyncpg.Connection, slug: str):
        member_id = await conn.fetchval(
            "select id from members where slug = $1", slug
        )
        if member_id is None:
            raise UnknownMember(slug)
        return member_id

    async def _mark_dirty(self, conn: asyncpg.Connection) -> None:
        await conn.execute(
            "update sync_state set dirty_since = coalesce(dirty_since, now())"
            " where id = 1"
        )

    async def record_dues(self, slug: str, year: int, actor: str) -> bool:
        """Record dues for a year. Returns False if already recorded.

        Reporting "already recorded" rather than silently succeeding matters:
        at a gathering the same member may be entered twice by two people, and
        the second person should know it was already done rather than assume
        they fixed something.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                member_id = await self._member_id(conn, slug)
                inserted = await conn.fetchval(
                    "insert into dues_paid (member_id, year, recorded_by)"
                    "     values ($1, $2, $3)"
                    "on conflict (member_id, year) do nothing"
                    "  returning true",
                    member_id, year, actor,
                )
                if inserted:
                    await self._mark_dirty(conn)
                return bool(inserted)

    async def set_proficiency(self, slug: str, kind: str, name: str,
                              level: int, actor: str) -> int | None:
        """Set a proficiency level. Returns the previous level, or None if new.

        The composite foreign key rejects a name that is not defined for the
        kind, which is turned into UnknownProficiency so the command can say so
        rather than surfacing a database error.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                member_id = await self._member_id(conn, slug)
                previous = await conn.fetchval(
                    "select level from proficiencies"
                    " where member_id = $1 and kind = $2 and name = $3",
                    member_id, kind, name,
                )
                try:
                    await conn.execute(
                        "insert into proficiencies (member_id, kind, name, level)"
                        "     values ($1, $2, $3, $4)"
                        "on conflict (member_id, kind, name)"
                        "  do update set level = excluded.level",
                        member_id, kind, name, level,
                    )
                except asyncpg.ForeignKeyViolationError as exc:
                    raise UnknownProficiency(kind, name) from exc
                await self._mark_dirty(conn)
                return previous

    async def set_flag(self, slug: str, field: str, value: bool,
                       actor: str) -> None:
        column = FLAG_COLUMNS.get(field)
        if column is None:
            # Not user input -- a caller bug. Never interpolate an arbitrary
            # string into SQL.
            raise StoreError(f"{field!r} is not a settable flag")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                member_id = await self._member_id(conn, slug)
                await conn.execute(
                    f"update members set {column} = $2, updated_at = now()"
                    " where id = $1",
                    member_id, value,
                )
                await self._mark_dirty(conn)

    async def link_discord(self, slug: str, discord_user_id: int) -> str | None:
        """Link a member to a Discord account. Returns any slug it was taken
        from, so the command can report a move rather than a silent steal."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                member_id = await self._member_id(conn, slug)
                previous = await conn.fetchval(
                    "select slug from members where discord_user_id = $1"
                    "   and id <> $2",
                    str(discord_user_id), member_id,
                )
                if previous is not None:
                    await conn.execute(
                        "update members set discord_user_id = null"
                        " where slug = $1", previous,
                    )
                await conn.execute(
                    "update members set discord_user_id = $2, updated_at = now()"
                    " where id = $1",
                    member_id, str(discord_user_id),
                )
                return previous

    async def known_proficiencies(self, kind: str) -> list[str]:
        async with self.pool.acquire() as conn:
            return [
                r["name"] for r in await conn.fetch(
                    "select name from proficiency_defs where kind = $1"
                    " order by name", kind,
                )
            ]
