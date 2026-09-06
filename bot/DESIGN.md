# Northern Steppes Discord Bot — Design

Status: **built**. This describes what exists, and why it is shaped this way.
For what is switched off, see [`../DEFERRED.md`](../DEFERRED.md); for how to
run it, [`README.md`](README.md).

## Goal

Let leadership maintain member records from Discord instead of hand-editing
TOML, and give members self-service access to their own proficiency sheet.

The motivating bug: `templates/members.html` listed someone under "Current
Members" only if `extra.dues[<current year>]` was true. No member file had a
2026 entry, so the roster was empty from January and every member — leadership
included — displayed as a past member. Fixing it meant editing 21 files by
hand, which is exactly why it had not happened.

## Architecture

One store, holding every member record.

```
Discord  ──slash command──▶  Bot (Railway)  ──write──▶  Postgres (Railway)
                                  │                          │
                                  │  read-only HTTP API      │
                                  ▼                          │
                          browser  ◀───── fetch ─────────────┘
                             ▲
                             │
                     GitHub Pages (the rest of the site, built statically)
```

**Postgres holds member records, and nothing else does.** There are no member
files in the repository. A command writes a row; the next read — from the bot
or from the website — sees it. Nothing renders, commits, or reconciles in
between.

**The site fetches the roster and member pages in the browser.** Everything
else on the site is still an ordinary static Zola build; only member data
comes from the API.

**The rank rules live in one place**, `northernsteppes_bot/ranks.py`, ported
from `content/proficiencies/`. Pure functions, no database or Discord, so they
are testable directly.

### This replaced an earlier design, and why

The first version of this document specified something different: Postgres as
a write buffer, **git as the canonical record**, and a debounced sync
rendering rows back into `content/members/_*.md` and committing them. The site
would build from those files as it always had, keeping an audit trail and
working without the bot being up.

It was built, then abandoned. The reasons are worth keeping, because they are
the argument against rebuilding it:

- **Two sources of truth produced every bug in the project.** `/dues` wrote
  the database while `/roster` read the file cache, so one command reported
  success and the next disagreed. A member created by the bot had no page
  until a sync committed one. Each was fixed individually; each was the same
  bug.
- **The rank rules had to exist twice** — Python for the bot, Tera macros for
  the build — with a parity test whose entire job was catching them drift
  apart. That test also required a full Zola install in CI.
- **The audit trail argument did not survive contact.** What git recorded was
  the *sync job* rewriting files, not who awarded what. `recorded_by` on the
  row is the audit trail, and it is more accurate.
- **The freshness argument was the wrong way round.** The sync was justified
  as free because both paths took ~90 seconds. In practice a reader wants the
  roster right *now*, and the debounce window was latency added on top of a
  deploy.

What was genuinely lost: member pages are no longer indexable or readable
without scripting, and **git is no longer a backup of member data**.
`backup.py` exists because of that second one.

### Why not the other alternatives

| Option | Why not |
|---|---|
| Zola `load_data(url=...)` at build time | Verified working on 0.22.1, `required=false` degrading cleanly. But it freezes member data at build time, which is the problem being solved. |
| Commit per command | One deploy per command. `publish.yaml` sets `cancel-in-progress: false`, so recording dues for 21 members would queue 21 sequential deploys. |
| Daily cron opening a PR | Up to 24h plus a human merge before a `/dues` shows on the site. |

## Schema

`migrations/*.sql`, plain files applied once each in filename order, tracked
by name in `schema_migrations`. Deliberately smaller than a migration
framework — a volunteer should be able to read the whole mechanism in a
minute. Note the corollary: **files are tracked by name, not by content**, so
editing an already-applied migration does nothing to an existing database.

| Table | Holds |
|---|---|
| `members` | identity, slug, waiver, veteran garb, units, Discord link |
| `dues_paid` | one row per member-year; a row means paid |
| `proficiency_defs` | the permitted `(kind, name)` pairs |
| `proficiencies` | a member's level in one of those pairs |

Two shapes worth explaining:

**`dues_paid` has no `paid` column.** A row means paid; absence means not. A
boolean would allow a row saying "not paid", which is the same information as
no row and invites the two to disagree.

**`proficiencies` has a composite foreign key** onto `proficiency_defs`, so
the database rejects a proficiency the club does not define. That is what
turns a typo in `/award` into a message listing the valid names rather than a
row nobody notices. `known_proficiencies()` reads the same table for
autocomplete, so what a command offers and what it accepts cannot drift.

`sync_state` existed for the abandoned sync, and migration 003 drops it.

## Rank logic

`ranks.py`, the only implementation. Two rule corrections came out of writing
the rules down as testable code:

- **Harbinger through the non-combat route.** `content/proficiencies/index.md`
  describes it; the Tera macro gated Harbinger behind the combat route.
- **Dues gate on having *ever* paid**, not on a flag that read true for all 21
  members regardless of what anyone had paid. A member behind on this year has
  not un-earned their proficiencies; being behind is shown, not punished.

## Commands

| | |
|---|---|
| Read | `/rank`, `/gaps`, `/roster`, `/me` |
| Write | `/dues`, `/dues-remove`, `/award`, `/profession`, `/waiver`, `/veteran-garb`, `/link` |
| Admin | `/member-add` |

`/gaps` says exactly what a member still needs for their next rank — the one
thing the website could never do.

**Every write is correctable from Discord.** `/award` and `/profession` take
level 0 to clear one; `/waiver` and `/veteran-garb` take a boolean; `/link`
reassigns and reports who lost the account. `/dues-remove` exists because
recording dues only ever inserted, which made a year entered against the wrong
member permanent. It requires the year rather than defaulting to the current
one — recording happens in bulk, where the default is nearly always right,
while removing is rare and deliberate, and a default only creates a way to
clear the wrong year.

Commands register to one guild, so they appear immediately rather than taking
up to an hour to propagate.

## Identity and permissions

`/link` maps a Discord account to a member, and `/me` resolves through it.
Matching on display name instead would risk showing one person's sheet to
another.

Writes are gated on a leadership role **and** the configured guild, checked
inside every handler. Discord's `default_member_permissions` only hides a
command in the picker; it does not stop anyone who knows the name. The guild
check means a test instance invited to the real server cannot edit real
records — and it runs before the role check, so the wrong server says so
rather than leaking whether the caller would otherwise have had permission.

Everything dangerous fails closed. A malformed role id counts as *no role*,
never as *no restriction*: the safe reading of a typo is that nobody is
leadership, not that everybody is.

## The read API

Read-only and unauthenticated, serving only what the site already publishes.
`discord_user_id` never appears in a payload; two tests enforce that.

It starts **before** connecting to Discord, deliberately. The API needs
nothing from Discord, so a bad token, an outage or a login rate limit must not
take the website's member data down too. A 429 holds the process open rather
than exiting, because exiting has the host restart straight into another login
attempt — which is what earns the block in the first place.

`API_ALLOWED_ORIGINS` replaces the default origin list, so a fork deployed to
its own Pages URL needs no code change.

## Deployment

Railway: a Postgres, and the bot with root directory `bot/`. Migrations run at
startup, and `PORT` being set is what turns the API on.

The bot refuses to start without `DISCORD_TOKEN` or `DATABASE_URL`, and
refuses a token not shaped like a bot token. A bot that connects and finds
zero members looks healthy in the logs while answering every question wrongly.

Configuration is listed in [`README.md`](README.md).

## Backups

Git no longer holds member records, so `backup.py` is how they leave the
database — plain JSON, which survives a schema change and can be read without
a database to hand. Restore upserts by slug and **never deletes**, so
restoring an old snapshot over a live database cannot silently drop members
recorded since it was taken.

## Open questions

1. **`config.toml`'s `api_url` points at the test deployment.** It needs the
   production bot's URL before this reaches northernsteppes.com.
2. **Taking a backup from a laptop does not work.** `railway run` injects an
   internal `DATABASE_URL` that resolves only inside Railway, and this
   Postgres has no TCP proxy. Either give it one — which publishes the
   database behind its password — or register an SSH key and use
   `railway ssh`. See `README.md`.
3. **Year rollover.** "Current member" means dues recorded for the calendar
   year, so the roster empties every January until dues come in. Rank is
   unaffected — it gates on having ever paid — but the roster split still
   needs a call from leadership on whether a grace period should apply.
4. **The class system**, its counters and flags, and per-member units are
   defined but unwired. See [`../DEFERRED.md`](../DEFERRED.md).
