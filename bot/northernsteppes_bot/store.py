"""Database reads and writes for member state.

This is the only place member records live. There are no member files any
more, so a write here is the change -- there is nothing to render, commit or
reconcile afterwards, and the next read (from the bot or from the website's
API) sees it immediately.

`all_members` is the read side of everything the write commands do. It rebuilds
each `MemberSheet` from the rows: identity, waiver, veteran garb, units and
dues from `members` and `dues_paid`, and weapons, professions and classes from
`proficiencies`, split by `kind`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import asyncpg

from .ranks import MemberSheet

log = logging.getLogger(__name__)

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


class DuplicateMember(StoreError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"a member with slug {slug!r} already exists")
        self.slug = slug


class InvalidSlug(StoreError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"{slug!r} is not a usable slug")
        self.slug = slug


class UnknownProficiency(StoreError):
    def __init__(self, kind: str, name: str) -> None:
        super().__init__(f"{name!r} is not a known {kind}")
        self.kind, self.name = kind, name


class MemberStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # --- reads -------------------------------------------------------------

    async def all_members(self) -> list[MemberSheet]:
        """Every member, built from the database.

        The database is the only source now: the member files that used to
        back this are gone, so there is nothing to merge and nothing that can
        drift. Proficiencies of every kind live here, including the classes
        and professions that were previously left in the files.
        """
        async with self.pool.acquire() as conn:
            members = await conn.fetch(
                "select id, slug, display_name, waiver, veteran_garb, units,"
                "       member_since"
                "  from members where archived = false order by slug"
            )
            dues: dict = {}
            for r in await conn.fetch(
                "select member_id, year from dues_paid"
            ):
                dues.setdefault(r["member_id"], {})[r["year"]] = True

            profs: dict = {}
            for r in await conn.fetch(
                "select member_id, kind, name, level from proficiencies"
            ):
                profs.setdefault(r["member_id"], []).append(r)

        sheets = []
        for row in members:
            weapons, professions, classes = {}, {}, {}
            for p in profs.get(row["id"], []):
                if p["kind"] == "weapon":
                    weapons[p["name"]] = p["level"]
                elif p["kind"] == "profession":
                    professions[p["name"]] = p["level"]
                elif p["kind"] == "flag":
                    classes[p["name"]] = bool(p["level"])
                else:
                    classes[p["name"]] = p["level"]
            sheets.append(MemberSheet(
                slug=row["slug"],
                display_name=row["display_name"],
                waiver=row["waiver"],
                veteran_garb=row["veteran_garb"],
                units=list(row["units"] or []),
                weapons=weapons,
                professions=professions,
                classes=classes,
                dues_years=dues.get(row["id"], {}),
            ))
        return sheets

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
                return bool(inserted)

    async def forget_dues(self, slug: str, year: int, actor: str) -> bool:
        """Un-record dues for a year. Returns False if there was nothing there.

        The correction path for a year entered against the wrong member or the
        wrong year. Every other write command is already a setter that can be
        called again with the right value; this one only ever inserted, so a
        mistake was permanent.

        The row is deleted rather than flagged, because a member who did not
        pay should read as not having paid, and a tombstone would have to be
        excluded from every read to achieve that. The removal is logged with
        the actor so it is not silent.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                member_id = await self._member_id(conn, slug)
                deleted = await conn.fetchval(
                    "delete from dues_paid where member_id = $1 and year = $2"
                    " returning true",
                    member_id, year,
                )
        if deleted:
            log.info("dues for %s/%s removed by %s", slug, year, actor)
        return bool(deleted)

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


#: Slugs are URL segments (/member/?member=<slug>) and identify a member in
#: every command, so they are kept to what is unambiguous in both.
_SLUG_OK = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def slugify(name: str) -> str:
    """Derive a slug from a display name, matching the existing files.

    Accents are folded rather than replaced, so a name like "Quelen
    Guardabosque" written with an accent still slugifies to `quelen-...` --
    the convention the existing files use. Without this it would become
    `quel-n-...` and produce a mangled file name and URL.
    """
    folded = unicodedata.normalize("NFKD", name.strip().lower())
    folded = folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


async def _create(conn, slug: str, display_name: str):
    return await conn.fetchval(
        "insert into members (slug, display_name) values ($1, $2)"
        " returning id",
        slug, display_name,
    )


async def create_member(store: "MemberStore", display_name: str,
                        slug: str | None, actor: str) -> str:
    """Create a member record. Returns the slug used."""
    slug = (slug or slugify(display_name)).strip().lower()
    if not _SLUG_OK.match(slug):
        raise InvalidSlug(slug)

    async with store.pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                "select slug from members where slug = $1", slug
            )
            if existing:
                raise DuplicateMember(slug)
            await _create(conn, slug, display_name.strip())
    return slug
