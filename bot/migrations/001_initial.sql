-- Initial schema. See bot/DESIGN.md for the reasoning behind the split
-- between Postgres and git.
--
-- Applied by northernsteppes_bot.db.apply_migrations(), which runs each file
-- in this directory once, in filename order, inside a transaction.

create table if not exists members (
    id              uuid primary key default gen_random_uuid(),
    slug            text unique not null,   -- 'lamp' -> content/members/_lamp.md
    display_name    text not null,          -- TOML `title`
    discord_user_id text unique,            -- null until linked
    member_since    date,
    race            text,
    unit            text,
    waiver          boolean not null default false,
    veteran_garb    boolean not null default false,
    archived        boolean not null default false,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create table if not exists dues (
    member_id   uuid not null references members on delete cascade,
    year        int  not null,
    paid        boolean not null default true,
    recorded_by text not null,              -- discord user id, or 'bootstrap'
    recorded_at timestamptz not null default now(),
    primary key (member_id, year)
);

-- Weapons, classes, professions and the class sub-counters share one table.
--   'weapon'     0-3 proficiency level
--   'class'      0-3 proficiency level
--   'profession' 0-3 proficiency level
--   'counter'    Light_Armor / Armor, unbounded ints
--   'flag'       Steal_10 / Steal_20 / Steal_30 / Look_Part, 0 or 1
create table if not exists proficiencies (
    member_id uuid not null references members on delete cascade,
    kind      text not null check (
                  kind in ('weapon', 'class', 'profession', 'counter', 'flag')
              ),
    name      text not null,
    level     int  not null default 0 check (level >= 0),
    primary key (member_id, kind, name)
);

-- Append-only audit log. This is the reason awards belong in a database:
-- git records that a value changed, this records who changed it and why.
create table if not exists awards (
    id         bigserial primary key,
    member_id  uuid not null references members on delete cascade,
    kind       text not null,
    name       text not null,
    old_level  int,
    new_level  int not null,
    awarded_by text not null,
    awarded_at timestamptz not null default now(),
    note       text
);

create index if not exists awards_member_idx on awards (member_id, awarded_at desc);

create table if not exists practices (
    id       bigserial primary key,
    held_on  date not null,
    location text,
    unique (held_on, location)
);

create table if not exists attendance (
    practice_id   bigint not null references practices on delete cascade,
    member_id     uuid   not null references members on delete cascade,
    checked_in_at timestamptz not null default now(),
    primary key (practice_id, member_id)
);

create table if not exists event_rsvps (
    event_slug   text not null,             -- matches content/events/<slug>
    member_id    uuid not null references members on delete cascade,
    response     text not null check (response in ('yes', 'no', 'maybe')),
    responded_at timestamptz not null default now(),
    primary key (event_slug, member_id)
);

-- Single-row table tracking whether the rendered member files are behind the
-- database, and when they were last reconciled.
create table if not exists sync_state (
    id              int primary key default 1 check (id = 1),
    dirty_since     timestamptz,
    last_synced_at  timestamptz,
    last_commit_sha text
);

insert into sync_state (id) values (1) on conflict (id) do nothing;
