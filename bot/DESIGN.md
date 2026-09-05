# Northern Steppes Discord Bot — Design

Status: **draft for review**. No code written yet.

## Goal

Let leadership maintain member records from Discord instead of hand-editing
TOML, and give members self-service access to their own proficiency sheet.

The immediate motivating bug: `templates/members.html` lists someone under
"Current Members" only if `extra.dues[<current year>]` is true. No member file
has a 2026 entry, so the roster has been empty since January and every member
displays under "Past Members". Updating dues means editing 21 files by hand,
which is exactly why it didn't happen.

## Architecture

Three stores, each holding what it is actually good at.

```
Discord  ──slash command──▶  Bot (Railway)  ──write──▶  Postgres (Railway)
                                  │                          │
                                  │  debounced sync          │
                                  ▼                          │
                          GitHub Git Data API                │
                                  │                          │
                                  ▼                          │
                      content/members/_*.md  ──push──▶  Actions ──▶ Pages
                                                               │
   client-side fetch ◀── read-only API (later, live data) ◀────┘
```

**Postgres is the write buffer and query store.** Commands write here and
return immediately, so `/rank` and `/roster` answer without a GitHub round
trip. It was also intended to hold the append-only, high-volume data —
attendance, RSVPs and an award audit log — though those tables are deferred
for now; see the note under Schema.

**Git stays the canonical record for member sheets.** The rendered
`content/members/_<slug>.md` files remain what the site builds from, so the
site never depends on Railway being up, hand-editing keeps working, and every
rank award lands in the commit history with a date and an author.

**The sync is debounced, not per-command.** One commit per command means one
deploy per command — recording dues for 21 members at a gathering would queue
21 sequential deploys, since `.github/workflows/publish.yaml` sets
`cancel-in-progress: false`. Instead the bot marks state dirty, waits for
quiet, and writes one commit containing everything that changed.

### Why not the alternatives

| Option | Why not |
|---|---|
| Zola `load_data(url=...)` against a Railway API | Verified working on 0.22.1, including graceful `required=false` degradation. But it trades away the git audit trail for freshness we don't gain — both paths are ~90s. |
| Commit per command | Deploy queue backlog, noisy history. |
| Daily cron opening a PR, as the primary path | Up to 24h latency plus a human merge before a `/dues` command shows up on the site. Kept instead as a reconciliation net, below. |
| Client-side fetch for the roster | The roster should be in the HTML for search engines and no-JS readers. Reserved for genuinely live data. |

## Schema

```sql
create table members (
    id              uuid primary key default gen_random_uuid(),
    slug            text unique not null,   -- 'lamp' -> content/members/_lamp.md
    display_name    text not null,          -- TOML `title`
    discord_user_id text unique,            -- null until linked
    member_since    date,
    units           text[] not null default '{}',  -- a member can be in several
    waiver          boolean not null default false,
    veteran_garb    boolean not null default false,
    archived        boolean not null default false,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- A row means "paid". There is no `paid` column because no member file has
-- ever recorded a year as false, so a false row would have no meaning: dues
-- are either recorded or they are not.
create table dues_paid (
    member_id   uuid not null references members on delete cascade,
    year        int  not null,
    recorded_by text not null,              -- discord user id, or 'bootstrap'
    recorded_at timestamptz not null default now(),
    primary key (member_id, year)
);

-- The set of proficiencies is fixed by content/proficiencies/ and effectively
-- never changes, so it is constrained in the database rather than only in
-- code. A seeded lookup table rather than an enum: a composite foreign key
-- can enforce that a 'weapon' row carries a weapon name, which an enum
-- cannot, and adding one later is an INSERT rather than an ALTER TYPE.
--
-- Weapons only for now -- see the deferral note below.
create table proficiency_defs (
    kind text not null check (kind in ('weapon')),
    name text not null,
    primary key (kind, name)
);

create table proficiencies (
    member_id uuid not null references members on delete cascade,
    kind      text not null,
    name      text not null,
    level     int  not null default 0 check (level >= 0),
    primary key (member_id, kind, name),
    foreign key (kind, name) references proficiency_defs (kind, name)
);

create table sync_state (
    id              int primary key default 1 check (id = 1),
    dirty_since     timestamptz,
    last_synced_at  timestamptz,
    last_commit_sha text
);
```

`proficiency_defs` is seeded in the same migration, so a typo in a command
cannot invent a proficiency the site templates do not render — the insert
fails rather than silently creating a "Sword and Board" that never displays.

**Deferred for now:** `awards`, `practices`, `attendance` and `event_rsvps`,
plus the `class`, `profession`, `counter` and `flag` proficiency kinds.

The proficiency kinds are deferred because that system is being reworked.
Defining none of them means the composite foreign key makes it impossible to
assign one to a member and then have to clean it up later. The eleven weapon
styles are seeded and match `content/proficiencies/combat-styles.md` and the
`[extra.weapons]` tables exactly.

Nothing is removed from the member files, so no data is lost — those fields
simply stay hand-edited and outside the bot's control until the rework lands.

**This splits where the read commands get their data.** `rank()` needs
professions (the route to Savage) and classes (the scout, soldier and thief
ladders), and neither is in the database. The rank rules already operate on a
`MemberSheet` parsed from the member files rather than on database rows, so
`/rank` and `/gaps` read weapons and dues from Postgres and the rest from the
files. That is workable but it is a second data path, and it should collapse
back into one once the proficiency rework lands.

Worth noting what deferring the four tables costs: those were the append-only,
genuinely database-shaped data. What remains is a mirror of data that already
lives in git. The database still earns its place as the write buffer that makes
the debounced sync possible, and as the query store behind instant `/roster`
replies — but if those four stay deferred indefinitely, reading the member
files straight from GitHub would be a reasonable simplification.

Dropping `awards` also means the record of *who* awarded a proficiency now
lives only in the git commit that recorded it, rather than in a queryable
table. For a club awarding ranks at monthly gatherings that is probably
enough, and the commit trail is durable, but it is a real reduction.

## Sync

Every write command sets `sync_state.dirty_since = coalesce(dirty_since, now())`.

An `asyncio` task in the bot process wakes every 60s and syncs once
`now() - dirty_since > SYNC_DEBOUNCE_SECONDS` (default 300):

1. Read the current `content/members/_*.md` from GitHub at a known ref SHA.
2. For each member, load the existing frontmatter with **`tomlkit`**, mutate
   only the changed keys, and dump. tomlkit preserves style, so key order,
   alignment and blank lines survive and the diff shows only real changes.
3. Drop any file whose rendered bytes equal the current bytes.
4. If nothing remains, clear `dirty_since` and stop — no empty commits.
5. Otherwise write **one** commit containing all changed files via the Git Data
   API (blobs → tree → commit → update ref), so a gathering's worth of edits is
   a single reviewable change.
6. On a ref conflict, re-read and retry, up to 3 times.
7. Record `last_commit_sha`, clear `dirty_since`.

The existing `push` trigger on `main` deploys it. No new workflow trigger is
needed for this path.

Commit message format:

```
chore(members): record dues for 3 members, 2 proficiency awards

Dues 2026: kimba, rhino, saewyn
Awards:    lamp Flail 2->3, goose "Sword & Board" 1->2

Recorded via Discord by <display name> and <display name>.
```

**Guardrails.** The bot only ever writes files under `content/members/`.
`DRY_RUN` logs the diff without committing, and `SYNC_ENABLED=false` is a kill
switch that leaves commands working while stopping all writes.

### Reconciliation net

A scheduled GitHub Actions workflow (daily) compares Postgres against the
committed files and opens a PR **only when they have drifted**.

This lives in Actions rather than in the bot deliberately: it is a safety net
for "the bot failed to sync", so it must not share the bot's failure modes. If
it cannot reach the database at all, the failed run is itself the alert.

A PR that appears only when something is genuinely wrong is a PR people will
read. A daily PR that is usually empty trains everyone to rubber-stamp it.

## Rank logic

The rank rules currently exist once, as the `rank()` macro in
`templates/ranks.html`. The bot needs them too, for `/rank` and `/gaps`.

Rather than pick one home, keep both and **test that they agree**. The site
keeps its self-contained Tera implementation so it never depends on the bot;
the bot gets a tested Python implementation; and CI builds the site and asserts
the Tera-rendered rank equals the Python-computed rank for every member. Any
drift fails the build.

This is cheap — extracting rendered ranks from built HTML is already a solved
problem here, having been used to verify the `rank()` macro refactor in #5.

## Commands

Leadership-gated. The role check runs **inside the handler** against
`LEADERSHIP_ROLE_ID`; `default_member_permissions` only hides a command in the
picker, it does not secure it.

| Command | Effect |
|---|---|
| `/dues paid member: year:` | Records dues — fixes the empty-roster bug |
| `/award member: kind: name: level:` | Sets a proficiency |
| `/waiver member: signed:` | Sets waiver status |
| `/veteran-garb member: owns:` | Sets veteran garb |
| `/member-add name: slug:` | Creates a member and their file |
| `/link member: discord:` | Maps a member to a Discord account |
| `/sync-now` | Forces a sync, bypassing the debounce |

Open to everyone:

| Command | Effect |
|---|---|
| `/rank [member]` | Computed rank, and the reason for it |
| `/gaps [member]` | What is still needed for the next rank |
| `/roster [rank]` | Current members, grouped by rank |
| `/me` | Your own sheet |
| `/practice` | Next practice time and location |

`/gaps` is worth building early: the rank rules are precise enough to compute
exactly what someone is missing, which today is a conversation with leadership.

`/checkin` and `/rsvp` are dropped from this pass along with the tables they
would write to.

## Identity

Member files key on nickname (`_lamp.md`, `_kaigar.md`) with no stable ID.
`members.slug` preserves that mapping, `members.id` gives a stable key that
survives a rename, and `discord_user_id` is nullable so a member record can
exist before that person is linked — the roster predates the bot.

`/link` is leadership-gated: self-service linking would let anyone claim
another member's sheet.

## Deployment

Railway project, two services:

- **Postgres** — Railway-provisioned, injects `DATABASE_URL`
- **Bot** — long-running worker, no public domain or exposed port
  - Root Directory: `bot/`
  - Watch Paths: `bot/**`, so site edits do not trigger a redeploy
  - Start: `python -m northernsteppes_bot`

Stack: Python 3.12, `discord.py`, `asyncpg`, `tomlkit`.

`tomlkit` is the load-bearing choice. The point of keeping records in git is a
readable audit trail, and a non-style-preserving TOML library would reformat
all 21 files on the first write, burying real changes in noise.

### Configuration

| Variable | Purpose |
|---|---|
| `DISCORD_TOKEN` | Bot token |
| `DISCORD_GUILD_ID` | Guild for command registration |
| `LEADERSHIP_ROLE_ID` | Role permitted to run write commands |
| `DATABASE_URL` | Injected by Railway |
| `GITHUB_APP_ID` / `GITHUB_INSTALLATION_ID` / `GITHUB_PRIVATE_KEY` | Repo write auth |
| `SYNC_DEBOUNCE_SECONDS` | Default 300 |
| `SYNC_ENABLED` | Kill switch |
| `DRY_RUN` | Log diffs without committing |

## Bootstrap

A one-off idempotent import parses the 21 existing member files into Postgres.
Git is the source of truth for this first load, and re-running it must be a
no-op. Write commands stay disabled until it has run.

## Open questions

1. **Repo write access.** The bot must write to `jackhumbert/northernsteppes`,
   which a fork cannot do. Preference is a GitHub App installed on the repo:
   scoped to this repo alone, revocable, and not tied to anyone's personal
   account. This currently blocks the sync path.
2. **Year rollover.** `dues[current_year]` is evaluated at build time, so the
   roster empties every January until dues are entered. Once `/dues` exists
   that is less painful, but a grace period (prior year counts until, say,
   March) or an explicit `active` flag would stop it recurring. Needs a call
   from leadership on what "current member" should mean.
3. **Bot-created members** — should `/member-add` create a file, or should new
   members always be added by hand first?
4. **Retiring race.** Race is a dead premise in the group, so there is no
   `race` column. But three member files still carry one (kaigar and magnus
   "Dwarf", meatwolf "DwarfGiant") and `templates/member.html:11` still
   renders it as "Race: Dwarf" on their pages. Once the sync job runs it will
   drop the field from those files and the template branch will simply stop
   matching -- which works, but retires the feature by erosion. Cleaner to
   remove the template line and the three frontmatter entries deliberately, as
   its own change. `unit` was genuinely unused by any template and is now
   `units text[]`.
