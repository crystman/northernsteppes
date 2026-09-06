"""Snapshot the member database, and restore one.

    python -m northernsteppes_bot.backup dump    > members.json
    python -m northernsteppes_bot.backup restore < members.json

The member files used to be the durable copy of these records: a fresh
database seeded itself from them, and git kept every change. With those files
gone the database is the only copy, so it needs a way out and a way back.

Format is plain JSON rather than a Postgres dump: it survives a schema change,
can be read without a database to hand, and diffs legibly in a commit if
somebody wants to keep snapshots that way.

Restore is additive and idempotent -- it upserts by slug and never deletes --
so restoring an old snapshot over a live database cannot silently drop members
recorded since. Emptying the database first is a deliberate, separate act.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone

import asyncpg

from . import db
from .config import Config

#: Tables in dependency order, so a restore never inserts a child before its
#: parent.
TABLES = ("members", "dues_paid", "proficiencies")


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


async def dump(pool: asyncpg.Pool) -> dict:
    snapshot: dict = {"format": 1, "taken_at": datetime.now(timezone.utc).isoformat()}
    async with pool.acquire() as conn:
        for table in TABLES:
            rows = await conn.fetch(f"select * from {table}")
            snapshot[table] = [
                {k: _plain(v) if not isinstance(v, (int, float, bool, type(None), str, list))
                 else v for k, v in dict(r).items()}
                for r in rows
            ]
    return snapshot


async def restore(pool: asyncpg.Pool, snapshot: dict) -> dict[str, int]:
    """Upsert a snapshot. Returns rows written per table."""
    if snapshot.get("format") != 1:
        raise SystemExit("unrecognised snapshot format")

    written = {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            slugs = {}
            for row in snapshot.get("members", []):
                member_id = await conn.fetchval(
                    """
                    insert into members
                        (slug, display_name, discord_user_id, member_since,
                         units, waiver, veteran_garb, archived)
                    values ($1, $2, $3, $4, $5, $6, $7, $8)
                    on conflict (slug) do update set
                        display_name = excluded.display_name,
                        units        = excluded.units,
                        waiver       = excluded.waiver,
                        veteran_garb = excluded.veteran_garb,
                        archived     = excluded.archived,
                        updated_at   = now()
                    returning id
                    """,
                    row["slug"], row["display_name"], row.get("discord_user_id"),
                    _as_date(row.get("member_since")), row.get("units") or [],
                    row["waiver"], row["veteran_garb"], row.get("archived", False),
                )
                slugs[row["id"]] = member_id
            written["members"] = len(slugs)

            for row in snapshot.get("dues_paid", []):
                member_id = slugs.get(row["member_id"])
                if member_id is None:
                    continue
                await conn.execute(
                    "insert into dues_paid (member_id, year, recorded_by)"
                    " values ($1, $2, $3)"
                    " on conflict (member_id, year) do nothing",
                    member_id, row["year"], row.get("recorded_by", "restore"),
                )
            written["dues_paid"] = len(snapshot.get("dues_paid", []))

            for row in snapshot.get("proficiencies", []):
                member_id = slugs.get(row["member_id"])
                if member_id is None:
                    continue
                await conn.execute(
                    "insert into proficiencies (member_id, kind, name, level)"
                    " values ($1, $2, $3, $4)"
                    " on conflict (member_id, kind, name)"
                    " do update set level = excluded.level",
                    member_id, row["kind"], row["name"], row["level"],
                )
            written["proficiencies"] = len(snapshot.get("proficiencies", []))
    return written


def _as_date(value):
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def _reachable_database_url() -> str | None:
    """The URL to connect on, preferring one that resolves from out here.

    `railway run` injects the service's own variables, and its DATABASE_URL
    points at `postgres.railway.internal` -- a name that only resolves inside
    Railway's network, so a backup taken from a laptop fails with a DNS error
    that says nothing about why. Railway also exposes DATABASE_PUBLIC_URL for
    exactly this case.
    """
    return (
        os.environ.get("DATABASE_PUBLIC_URL")
        or Config.from_env().database_url
    )


async def _run(action: str) -> int:
    database_url = _reachable_database_url()
    if not database_url:
        raise SystemExit(
            "DATABASE_URL is not set. Run under `railway run` to borrow the "
            "deployed service's variables."
        )
    pool = await db.connect(database_url)
    try:
        if action == "dump":
            json.dump(await dump(pool), sys.stdout, indent=1, default=_plain)
            sys.stdout.write("\n")
        else:
            written = await restore(pool, json.load(sys.stdin))
            print("restored:", written, file=sys.stderr)
    finally:
        await pool.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("dump", "restore"))
    return asyncio.run(_run(parser.parse_args(argv).action))


if __name__ == "__main__":
    sys.exit(main())
