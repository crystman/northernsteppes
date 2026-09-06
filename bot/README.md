# Northern Steppes bot — development

How to run, test and deploy the bot. Rationale for individual decisions lives
in the comments and docstrings next to the code they explain.

## Setup

Python 3.12.

```bash
cd bot
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt  # macOS/Linux
```

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored; on Railway
these are service variables instead. Nothing in it is needed to run the tests.

`DISCORD_TOKEN` is the **bot token**, from the developer portal's **Bot** tab
(Reset Token) — *not* the Client Secret on the OAuth2 tab, which is for logging
users into a web app and will fail here with "Improper token has been passed".
Discord shows a bot token only once, at creation or reset, so if it was not
saved then it has to be reset. Neither value belongs in this repo.

## Tests

```bash
cd bot
.venv/Scripts/python.exe -m pytest
```

Two groups:

| Group | Needs | Without it |
|---|---|---|
| Rank rules, views, config, API | nothing | always run |
| Store, schema, backup | a Postgres | skipped |

Skips are quiet by design, so the suite is usable without a database installed.
CI supplies one, so nothing is skipped there.

### Postgres

The store tests need a real database, because what they exercise is mostly in
the SQL — the `ON CONFLICT` that makes `/dues` idempotent, the composite
foreign key behind an unknown style, and the link handover that unlinks a
previous owner. Point `TEST_DATABASE_URL` at any throwaway database:

```bash
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/ns_bot_dev \
  .venv/Scripts/python.exe -m pytest
```

**The tests wipe the `public` schema on every run.** Never point this at
Railway, or at a database you care about.

Test fixtures are synthetic — a four-member cast covering each rank and each
dues state, defined in `tests/conftest.py`. Real member records live only in
the deployed database and never reach the repository, so no assertion here can
be broken by somebody's actual proficiencies changing.

#### A throwaway local instance

If you have PostgreSQL installed but would rather not touch your existing
cluster, run a second one on a spare port with trust auth. No password, no
admin rights, nothing shared with your real instances:

```bash
PGBIN="/c/Program Files/PostgreSQL/17/bin"
PGDATA="$TEMP/ns-pgdata"

"$PGBIN/initdb.exe" -D "$PGDATA" -U postgres --auth=trust --encoding=UTF8
"$PGBIN/pg_ctl.exe" -D "$PGDATA" -o "-p 55432 -c listen_addresses=127.0.0.1" \
    -l "$PGDATA/server.log" start
"$PGBIN/createdb.exe" -h 127.0.0.1 -p 55432 -U postgres ns_bot_dev
```

Stop it with `"$PGBIN/pg_ctl.exe" -D "$PGDATA" stop`. Because the data
directory lives under the temp directory it is genuinely disposable — delete it
and re-run the above to start clean.

Trust auth means anything on this machine can connect to that port without a
password. That is fine for a throwaway instance bound to 127.0.0.1 holding
nothing but test fixtures; do not configure a real database this way.

## What is deliberately switched off

The class system -- class names, the Light_Armor/Armor counters, the
Steal/Look_Part flags, and the Scout/Soldier/Thief ladders -- is defined in
the database but has no commands and is shown nowhere, because the club is
reworking it. Units are stored but never displayed. `../DEFERRED.md` lists
what is off, why, and what turning each back on would take.

## Where member records live

**In Postgres, and nowhere else.** There are no member files in this
repository. The bot reads and writes the database directly, and the website
reads the same rows through this bot's HTTP API — so a change recorded in
Discord is visible everywhere on the next request, with nothing to render,
commit or reconcile in between.

That has one consequence worth stating plainly: **the database is the only
copy.** Git is no longer a backup of member data. See "Backups" below.

## Running the bot

```bash
cd bot
DISCORD_TOKEN=... DATABASE_URL=... .venv/Scripts/python.exe -m northernsteppes_bot
```

It refuses to start rather than starting wrong: a missing `DISCORD_TOKEN`, a
token that is not shaped like a bot token, or a missing `DATABASE_URL` each
exit 1 with an explanation. A bot that connects and quietly finds zero members
looks healthy in Railway's logs while answering every question incorrectly.

Commands are registered to the guild rather than globally, so they appear
immediately instead of taking up to an hour to propagate.

### Trying it in your own server

Point it at a scratch guild and a scratch database and nothing real is at risk.

1. **Invite the bot.** In the Discord developer portal, under OAuth2 → URL
   Generator, tick the `bot` and `applications.commands` scopes, then `Send
   Messages` under bot permissions. Open the generated URL and pick your
   server. Or use this link directly — the Application ID is public and
   appears in every invite URL:

   ```
   https://discord.com/oauth2/authorize?client_id=1545624444402139146&permissions=2048&scope=bot+applications.commands
   ```

   No privileged intents are needed, so nothing requires Discord's approval.

2. **Point it at that server.** Commands register to one guild, and
   `DISCORD_GUILD_ID` defaults to Northern Steppes (`183746241098678273`), so
   set it to your test server's ID — otherwise the bot tries to register
   commands in a guild it is not in and fails with a permissions error. Enable
   Developer Mode in Discord, then right-click the server icon → Copy Server ID.

3. **Give it a database.** Any Postgres will do; migrations run at startup.
   A fresh one starts empty, so add somebody with `/member-add`.

Guild-scoped commands appear immediately, so `/rank`, `/gaps` and `/roster`
are usable as soon as it logs in.

`/me` will tell you it needs a member-to-Discord link. That is expected: the
link is set by `/link`, which is leadership-gated. Matching on a Discord
display name instead would risk showing one person's sheet to another.

### The read API

If `PORT` is set, the bot also serves a small read-only HTTP API — `/api/health`,
`/api/members`, `/api/members/<slug>` — which is how the website renders the
roster and each member page. It exposes only what the site already publishes;
`discord_user_id` is never in a payload, and two tests enforce that.

It is started **before** connecting to Discord, deliberately: a bad token, an
outage or a login rate limit must not take the website's member data down too.

`API_ALLOWED_ORIGINS` is a comma-separated list, replacing the defaults
entirely. A fork deployed to its own Pages URL needs its origin added there.

## Backups

The database is the only copy of member records, so it needs a way out and a
way back:

```bash
python -m northernsteppes_bot.backup dump    > members.json
python -m northernsteppes_bot.backup restore < members.json
```

Against the deployed database, without copying its URL anywhere:

```bash
railway run python -m northernsteppes_bot.backup dump > members.json
```

`railway run` injects the service's variables into a local command — but the
`DATABASE_URL` it hands out points at `postgres.railway.internal`, a name that
resolves only inside Railway's network. From a laptop that fails with a bare
DNS error. Two ways round it:

- **Give the Postgres service a TCP proxy** (its Settings → Networking). That
  publishes `DATABASE_PUBLIC_URL`; reference it on the bot service as
  `${{Postgres.DATABASE_PUBLIC_URL}}` and the command above works, because
  `backup.py` prefers it when present. Note this puts the database on the
  public internet behind its password.
- **Run it inside Railway instead**, which needs no public database:

  ```bash
  ssh-keygen -t ed25519         # once, if you have no key
  railway ssh keys add ~/.ssh/id_ed25519.pub
  railway ssh python -m northernsteppes_bot.backup dump > members.json
  ```

Plain JSON rather than a Postgres dump: it survives a schema change, can be
read without a database to hand, and diffs legibly if you want to keep
snapshots in a repository.

**Restore is additive and idempotent.** It upserts by slug and never deletes,
so restoring an old snapshot over a live database cannot silently drop members
recorded since it was taken. Emptying the database first is a separate,
deliberate act.

## Deploying to Railway

**Going to production for the first time? Read [RELEASING.md](RELEASING.md)
instead.** It covers the parts this section does not: which Discord
application, the public domain the API needs, and the order to do things in so
the website is not pointing at a bot that does not exist yet.

Two services in one project: a Postgres, and this bot.

Because `requirements.txt` lives in `bot/`, the service's **Root Directory
must be `bot/`** — that is a service setting rather than something
config-as-code can express.

Railway may build with either Nixpacks or Railpack. Both are covered: a
`Procfile` and a `railpack.json` give the start command, since Railpack does
not read `railway.json`'s builder hint and fails the build outright if it
cannot infer one.

Variables to set on the bot service:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | from the developer portal's Bot tab |
| `DISCORD_GUILD_ID` | the guild to register commands in |
| `LEADERSHIP_ROLE_NAME` | or `LEADERSHIP_ROLE_ID`, which is preferred |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — a reference, not a pasted literal, so it survives credential rotation |
| `API_ALLOWED_ORIGINS` | the site origins allowed to read the API |

Give the service a public domain if the website should read from it; Railway
then sets `PORT` and the API starts.

### What the logs should say

```
loaded settings from .../bot/.env      (local only; Railway uses variables)
database connected
leadership role 'Leadership' resolved to id 123456789
commands synced to guild 1279582837749842092
connected as NorthernSteppesBot | guild=... write-commands=enabled
```

If `DATABASE_URL` is set but unreachable, the bot logs

```
database unavailable; continuing read-only, write commands will refuse
```

and stays connected rather than crash-looping — but it can answer nothing
about members, because there is nothing else to read. **A bot that is online
is not proof the database connected**: check the log line.

### How a deploy happens

`.github/workflows/bot-deploy.yaml` runs `railway up` against production after
the tests pass on main, using a `RAILWAY_TOKEN` repository secret. Railway's
own GitHub integration is not working for this project, and deploying by hand
means production updates only when a particular person opens a particular
laptop.

Without the secret the workflow is a no-op that says so, so it is harmless
before the token exists. To deploy by hand anyway:

```bash
cd bot
railway up --environment production
```

## Identifying leadership

Write commands are gated on a single Discord role, configured either way:

| Variable | Behaviour |
|---|---|
| `LEADERSHIP_ROLE_ID` | Used directly. Stable across renames — prefer this. |
| `LEADERSHIP_ROLE_NAME` | Resolved to an id at startup against the guild's roles. |

The name is a convenience for when you cannot get the id (it needs Developer
Mode and access to the server). It resolves only on an **unambiguous** match,
compared case-insensitively:

- exactly one role with that name — resolved, and the id is logged so it can be
  pinned in `LEADERSHIP_ROLE_ID` later
- no role with that name — write commands stay disabled
- **several roles with that name** — write commands stay disabled

That last case is the reason ids are preferred. Discord permits duplicate role
names, and guessing between them is how write access ends up on the wrong role.
Renaming the role also silently breaks a name lookup, where an id keeps working.

Until the name resolves, the startup log says so:

```
write-commands=DISABLED (role 'Leadership' not resolved yet)
```

## Layout

```
bot/
├── northernsteppes_bot/
│   ├── config.py     environment, fail-closed permission gate
│   ├── db.py         connection pool, migration runner
│   ├── ranks.py      rank and class-ladder rules
│   ├── store.py      every read and write against Postgres
│   ├── views.py      command responses, as pure functions
│   ├── api.py        read-only HTTP API the website reads
│   ├── backup.py     JSON snapshot and restore
│   ├── bot.py        discord client and command handlers
│   └── __main__.py   entry point
├── migrations/       plain .sql, applied once each in filename order
└── tests/
```
