"""A small read-only HTTP API, so the website can show current member status.

The site is static and only as fresh as its last build. This lets a page
correct itself in the browser: the committed content renders as it always
did -- indexed, and working with scripting disabled -- and a fetch updates the
parts that go stale, chiefly dues.

Deliberately narrow:

* **Read only.** No route changes anything. Writes stay in Discord, behind the
  leadership role.
* **No authentication, and therefore no secrets.** Everything served here is
  already published on the website: names, ranks, dues status and proficiency
  levels all appear on the member pages today.
* **`discord_user_id` is never exposed.** It exists only because of the bot,
  it is not the website's business, and it would let anyone map a Discord
  account to a member.

Runs on `PORT` when set. Railway sets it for a service with a public domain;
with no port the bot stays a private worker as before.
"""

from __future__ import annotations

import logging
from typing import Iterable

from aiohttp import web

from .ranks import (
    LEVEL_NAMES,
    RANK_NAMES,
    MemberSheet,
    dues_state,
    gaps,
    rank,
    scout_rank,
    soldier_rank,
    thief_rank,
)

log = logging.getLogger(__name__)

#: Browsers refuse a cross-origin fetch without this, and the site is served
#: from a different origin than the API. Read-only and public, so the value is
#: not a control -- it is set explicitly rather than left to a wildcard so the
#: intent is visible.
ALLOWED_ORIGINS = (
    "https://northernsteppes.com",
    "https://www.northernsteppes.com",
    "http://127.0.0.1:1111",   # zola serve
    "http://localhost:1111",
)

#: A stale page is better than a hammered API; the data changes rarely.
CACHE_SECONDS = 60


def member_json(sheet: MemberSheet, year: int) -> dict:
    """One member, in the shape a page needs to correct itself."""
    overall = rank(sheet)
    return {
        "slug": sheet.slug,
        "name": sheet.display_name or sheet.slug,
        "rank": RANK_NAMES[overall],
        "dues": {
            "state": dues_state(sheet),
            "year": year,
            "paid_years": sorted(y for y, paid in sheet.dues_years.items() if paid),
        },
        "waiver": sheet.waiver,
        "veteran_garb": sheet.veteran_garb,
        "units": list(sheet.units),
        "weapons": {
            name: LEVEL_NAMES[level]
            for name, level in sorted(sheet.weapons.items()) if level
        },
        "classes": {
            "scout": scout_rank(sheet, overall),
            "soldier": soldier_rank(sheet, overall),
            "thief": thief_rank(sheet, overall),
        },
        "gaps": gaps(sheet),
    }


def _cors(request: web.Request, response: web.Response) -> web.Response:
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return response


def build_app(sheets_provider, year_provider) -> web.Application:
    """An app serving whatever `sheets_provider()` currently returns.

    Both are callables rather than values so the API always reflects the
    bot's current state, including database writes, without holding its own
    copy that could drift.
    """

    async def health(request: web.Request) -> web.Response:
        return _cors(request, web.json_response({"status": "ok"}))

    async def members(request: web.Request) -> web.Response:
        year = year_provider()
        sheets: Iterable[MemberSheet] = await sheets_provider()
        return _cors(request, web.json_response({
            "year": year,
            "members": [member_json(s, year) for s in sheets],
        }))

    async def member(request: web.Request) -> web.Response:
        slug = request.match_info["slug"].lower()
        year = year_provider()
        sheets = await sheets_provider()
        found = next((s for s in sheets if s.slug.lower() == slug), None)
        if found is None:
            return _cors(request, web.json_response(
                {"error": "no such member", "slug": slug}, status=404
            ))
        return _cors(request, web.json_response(member_json(found, year)))

    async def preflight(request: web.Request) -> web.Response:
        response = web.Response(status=204)
        response.headers["Access-Control-Allow-Methods"] = "GET"
        return _cors(request, response)

    app = web.Application()
    app.add_routes([
        web.get("/api/health", health),
        web.get("/api/members", members),
        web.get("/api/members/{slug}", member),
        web.options("/api/{tail:.*}", preflight),
    ])
    return app


async def start(app: web.Application, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("read API listening on port %s", port)
    return runner
