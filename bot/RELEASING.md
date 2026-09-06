# Releasing the bot to production

Read this before merging the bot PR. Since member records moved into the
database, **northernsteppes.com's member pages depend on this service being
up** — the roster and every member page are fetched from its API in the
browser. That is a new kind of dependency for a site that used to be pure
static, and it drives everything below.

The order matters. Merging the site changes before a production bot exists
breaks `/members/` on the next Pages deploy.

---

## Who owns it

The production bot and its database should live in a **Railway workspace both
maintainers can administer**, not in one person's personal projects. Whoever
owns the domain needs to be able to restart the service, read its logs and
rotate its token without going through anybody.

Railway team seats are a paid plan — check the cost before committing to this
route. The alternative, if that is not worth paying for, is that the person
who owns the domain also owns the Railway account, and the other maintainer
gets deploy access through the GitHub Action below rather than through
Railway itself.

---

## One-time setup

### 1. Share the Railway project

Move the `northern steppes` project into a workspace both maintainers belong
to, and invite the other maintainer. Nothing in the project needs to change;
`production` already has a Postgres and a bot service.

### 2. Create a second Discord application

**Test and production cannot share a Discord token.** Discord issues one
gateway session per shard, so two instances running the same token will
either invalidate each other's sessions or split interactions between them
unpredictably. Neither failure is obvious from the outside — it looks like the
bot intermittently ignoring commands.

Create a second application in the Discord developer portal, so one token
belongs to test and the other to production. Invite it to the real server with
the `bot` and `applications.commands` scopes and Send Messages. No privileged
intents, so nothing needs Discord's approval.

### 3. Give the production service a public domain

**This is the step that makes the website work.** Railway sets `PORT` only for
a service with a domain, and the bot starts its API only when `PORT` is set.
Without it the bot runs, commands work, and every member page on the website
says it cannot load the roster.

Railway → the bot service → Settings → Networking → Generate Domain. Note the
URL; several things below need it.

### 4. Set the production variables

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | the **production** application's bot token, from its Bot tab |
| `DISCORD_GUILD_ID` | `183746241098678273` (the real server). Set it explicitly — it is also the default, but an unset value logs a warning precisely because a test deployment that forgot it would point at production. |
| `LEADERSHIP_ROLE_ID` | the real server's Leadership role id. Prefer the id over `LEADERSHIP_ROLE_NAME`: it survives a rename, and duplicate role names refuse to resolve. |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — a reference, not a pasted literal, so it survives credential rotation |
| `API_ALLOWED_ORIGINS` | optional. The defaults already cover northernsteppes.com and www; set it only to add another origin. |

Never paste a token into a chat, an issue or a commit. If one gets out, reset
it on the Bot tab — a leaked bot token lets anyone act as the bot in the
server.

### 5. Point the site at the production API

In `config.toml`:

```toml
api_url = "https://<the domain from step 3>"
```

It currently points at the **test** deployment. Left as-is, northernsteppes.com
would read its roster out of a scratch database.

### 6. Add the deploy token

`.github/workflows/bot-deploy.yaml` runs `railway up` after the tests pass on
main. It needs `RAILWAY_TOKEN` under Settings → Secrets and variables →
Actions in this repository.

Use a **project token scoped to the production environment**, not an account
token: it can only deploy this one environment, and it is not tied to a
person's account. Railway → project → Settings → Tokens.

Optionally also add a repository *variable* `BOT_API_URL` set to the same URL
as step 5. The workflow then polls `/api/health` after deploying and fails if
the service does not come up, instead of reporting a green deploy for a bot
that is crash-looping.

Until the secret exists the workflow is a no-op that says so in the run
summary, so merging before this is done is harmless.

---

## Release order

1. **Merge #31** — forks build for their own Pages URL. Not strictly required
   for production, but it is what keeps fork previews honest, and it is a
   one-line change.
2. **Do the one-time setup above**, through step 4. Do not merge yet.
3. **Deploy the bot to production once, by hand**, to prove the environment
   before anything depends on it:

   ```bash
   cd bot
   railway up --environment production
   ```

   Then check the log says `database connected`, `commands synced to guild
   183746241098678273`, `write-commands=enabled`, and `read API listening`.
   Curl the health endpoint:

   ```bash
   curl https://<domain>/api/health
   ```

4. **Seed the production database.** It starts empty — migrations create the
   schema but no members. Either restore a snapshot:

   ```bash
   python -m northernsteppes_bot.backup restore < members.json
   ```

   or add members with `/member-add` and record their dues and proficiencies
   through the commands. Note that proficiencies are being restarted anyway,
   so a fresh start is a legitimate choice here.

5. **Now** do step 5 (`config.toml`) and merge the bot PR.
6. **Watch the first Pages deploy** and load
   https://northernsteppes.com/members/. If it says the roster could not be
   loaded, the API is unreachable or the origin is not allowed — check the
   bot's logs for the request.

---

## Verifying a release

- `https://<domain>/api/health` returns `{"status": "ok"}`
- `https://northernsteppes.com/members/` lists current members
- A member page — `https://northernsteppes.com/member/?member=<slug>` — shows
  rank, styles and professions
- `/roster` in the real Discord server agrees with the website
- `/dues` on a test member, then reload the members page: the change shows
  without a deploy

## When something is wrong

**The members page says the roster could not be loaded.** The API is down or
the origin is refused. `railway logs` will show the request if it arrived; if
it did not, the domain or `PORT` is the problem.

**The bot is online but write commands refuse.** `LEADERSHIP_ROLE_ID` is
unset or wrong, or the database is unreachable. The startup line says which:
`write-commands=DISABLED (...)` names the reason.

**Commands are missing from the picker.** They register to one guild at
startup; check `commands synced to guild <id>` names the right server.

**The bot keeps restarting after a token change.** Discord rate-limits
repeated logins, and the bot deliberately holds for five minutes after a 429
so a restart does not immediately retry. Wait it out rather than redeploying.

**Losing the database loses the member records.** Git is no longer a backup.
See "Backups" in [README.md](README.md), and note the open problem there about
taking one from a laptop.
