# Deferred and removed features

Things this project deliberately does not do yet, and things it used to do and
no longer does. Written down because both categories look like bugs otherwise:
a field the database carries but nothing shows, a rule the site describes but
no command can grant, a file somebody remembers editing that is gone.

Nothing here is broken. Each entry says why it is off and what turning it back
on would take.

---

## Deferred: the class system

The club is reworking it, so none of it is wired up. The site still describes
it in `content/proficiencies/classes.md`, which is the intended future state,
not the current one.

### Class and prestige names

The 14 names — Scout, Archer, Ranger, Vanguard, Soldier, Berserker, Paladin,
Shaman, Shieldman, Spearman, Thief, Assassin, Rogue, Swashbuckler.

| | |
|---|---|
| **State** | Defined in the database, never set |
| **Where** | `bot/migrations/002_all_proficiency_kinds.sql`, kind `class` |
| **Why deferred** | The rework will likely change the names and what they require. Awarding them now would create records to migrate later. |
| **To enable** | A command calling `store.set_proficiency(slug, "class", name, level, actor)`. The definitions and the storage already exist; only the command is missing. |

### Light_Armor and Armor counters

Numbers, not levels — they count qualifying pieces, and gate the Scout and
Soldier ladders at 3 and 6.

| | |
|---|---|
| **State** | Defined in the database, never set |
| **Where** | `bot/migrations/002_all_proficiency_kinds.sql`, kind `counter` |
| **Why deferred** | Part of the same rework. They also do not fit the 0–3 shape every existing command uses, so a command for them needs a different range. |
| **To enable** | A command with a wider level range than `app_commands.Range[int, 0, 3]`. |

### Steal_10 / Steal_20 / Steal_30 / Look_Part flags

Yes-or-no, gating the Thief ladder.

| | |
|---|---|
| **State** | Defined in the database, never set |
| **Where** | `bot/migrations/002_all_proficiency_kinds.sql`, kind `flag` |
| **Why deferred** | Same rework. |
| **To enable** | A command storing 0 or 1, or a boolean option mapped to those. |

### The Scout / Soldier / Thief ladders

Derived ranks, computed from weapon levels plus the counters and flags above.

| | |
|---|---|
| **State** | Computed and served, shown nowhere |
| **Where** | `scout_rank`, `soldier_rank`, `thief_rank` in `bot/northernsteppes_bot/ranks.py`; sent as `classes` by the API |
| **Why deferred** | With the counters unawardable, the ladders move only on weapon levels — so they would state a rank nobody can act on or advance. |
| **To enable** | Re-add the `Classes` block to `views.format_rank`, and a `member-classes` section to `templates/member_live.html`. Two tests assert it is absent and will fail, which is intended — delete them as part of the change. |

The API still sends `classes`, deliberately. It is derived from data that is
already public, and keeping it means re-enabling the display is a template
change with no API change.

---

## Deferred: not part of the rework

### Units

Which unit a member belongs to — CoWS and so on.

| | |
|---|---|
| **State** | Stored, served by the API, shown nowhere |
| **Where** | `members.units`; in the API payload as `units` |
| **Why deferred** | Carried through the migration off the member files so the data was not lost. No page or command has asked for it since. |
| **To enable** | Render `m.units` on the member page. Nothing else is needed; the data is there. |

There is also no command to set it, so it can only be edited in the database.
If units start mattering, that gap needs closing at the same time.

---

## Removed: not coming back in this form

### Member files

`content/members/_*.md` — one committed Markdown file per member.

Replaced by the database. See `bot/README.md`. The consequence to remember is
that **git is no longer a backup of member data**; `bot/northernsteppes_bot/backup.py`
is how records leave the database now.

### The git sync

A job that rendered database rows back into member files and committed them.
Removed with the files it wrote — there is nothing to render. Its
configuration (`SYNC_ENABLED`, `DRY_RUN`, `GITHUB_TOKEN`, `SYNC_REPO`,
`SYNC_BRANCH`) and the `/sync-status` command are gone, and migration 003
drops the `sync_state` table. Stale entries may still sit in `bot/.env.example`;
they are ignored.

### The Tera rank macros

`templates/ranks.html` implemented the rank rules a second time so the site
could compute ranks at build time, with a parity test asserting the two
implementations agreed. The site now asks the bot through the API, so
`bot/northernsteppes_bot/ranks.py` is the only implementation and there is
nothing to keep in agreement. This is also why CI no longer installs Zola.

### Race

Removed as a dead premise — the club does not use it. Not deferred; it is not
coming back.

### Per-member built pages

`/members/<slug>/` used to be a static page per member file. Now one page,
`/member/?member=<slug>`, fetches whoever it is asked for. The trade is that
member pages are no longer indexable and need scripting; in exchange they
exist for every member the moment leadership adds one, rather than after a
commit.

---

## Related

- Four style names appear in `content/proficiencies/classes.md` but are
  deliberately not seeded as proficiencies: Florentine (the older name for
  Dual Wield), Glaive (a large red weapon, not a style), and Red and Blue
  (weapon construction categories). The reasoning is recorded in
  `bot/migrations/001_initial.sql`.
- `bot/tests/test_proficiency_links.py` asserts every name the migrations can
  seed has a matching section on the site — including the deferred kinds, so
  the write-ups stay in step and re-enabling stays cheap.
