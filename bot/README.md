# Northern Steppes bot — development

Design and rationale live in [DESIGN.md](DESIGN.md). This file is just how to
run things.

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

Three groups, with different requirements:

| Group | Needs | Without it |
|---|---|---|
| Rank rules, config | nothing | always run |
| Parity vs the site's Tera macros | Zola 0.22.1 | skipped |
| Importer / schema | a Postgres | skipped |

Skips are quiet by design so the suite is usable with neither installed. CI has
both and passes `--require-zola`, which turns the Zola skip into a failure so
the parity check cannot pass vacuously.

### Zola

The parity test builds the real site, so it needs the version pinned in
`.github/workflows/publish.yaml` — currently **0.22.1**. It reads that pin
itself rather than trusting `PATH`, and reports e.g. `found 0.23.4, need
0.22.1` rather than silently comparing against a failed build. See "Local
Development" in the repo readme for install instructions.

### Postgres

The importer tests need a real database, because what they exercise is mostly
in the SQL — the `ON CONFLICT` upserts, the composite foreign key, and the
`xmax = 0` check that distinguishes an insert from an update. Point
`TEST_DATABASE_URL` at any throwaway database:

```bash
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/ns_bot_dev \
  .venv/Scripts/python.exe -m pytest
```

**The tests wipe the `public` schema on every run.** Never point this at
Railway, or at a database you care about.

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

## Running the bot

```bash
cd bot
DISCORD_TOKEN=... .venv/Scripts/python.exe -m northernsteppes_bot
```

It refuses to start rather than starting wrong: no token, a missing
`content/members` directory, or an empty roster each exit 1 with an
explanation. A bot that connects and quietly finds zero members looks healthy
in Railway's logs while answering every question incorrectly.

Commands are registered to the guild rather than globally, so they appear
immediately instead of taking up to an hour to propagate.

### Trying it in your own server

The read-only commands need no database, no leadership role and no repo
access, so they are safe to point at a scratch server.

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

3. **Run it locally**, from a repo checkout so it can read `content/members`:

   ```bash
   cd bot
   .venv/Scripts/python.exe -m northernsteppes_bot
   ```

Guild-scoped commands appear immediately, so `/rank`, `/gaps` and `/roster`
are usable as soon as it logs in.

`/me` will tell you it needs a member-to-Discord link. That is expected: the
link is set by `/link`, a leadership-gated write command that does not exist
yet. Matching on a Discord display name instead would risk showing one
person's sheet to another.

### Where read commands get their data

From the member files, not the database. Nothing writes to the database yet
except the bootstrap import, which reads those same files, so the two cannot
disagree -- and the fields the rank rules lean on most (professions, and the
counters behind the class ladders) are deliberately absent from the database
while that system is reworked. When writes start flowing through Postgres this
becomes a query and `MemberDirectory` loses its file path.

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
│   ├── importer.py   bootstrap import from content/members/
│   ├── members.py    parse member files via tomlkit
│   ├── ranks.py      rank and class-ladder rules
│   ├── roster.py     in-memory member lookup
│   ├── views.py      command responses, as pure functions
│   ├── bot.py        discord client and command handlers
│   └── __main__.py   entry point
├── migrations/       plain .sql, applied once each in filename order
└── tests/
```
