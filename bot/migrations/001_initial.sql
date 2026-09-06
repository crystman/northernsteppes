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
    -- No `race` column: the concept is retired in the group. Three member
    -- files still carry one and templates/member.html still renders it, so
    -- retiring those is a separate change -- see DESIGN.md.
    -- Plural: a member can belong to more than one unit.
    units           text[] not null default '{}',
    waiver          boolean not null default false,
    veteran_garb    boolean not null default false,
    archived        boolean not null default false,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- A row means "paid". There is deliberately no `paid` column: no member file
-- has ever recorded a year as false, so a false row would have no meaning.
-- Dues are either recorded or they are not.
create table if not exists dues_paid (
    member_id   uuid not null references members on delete cascade,
    year        int  not null,
    recorded_by text not null,              -- discord user id, or 'bootstrap'
    recorded_at timestamptz not null default now(),
    primary key (member_id, year)
);

-- The set of proficiencies is fixed by content/proficiencies/ and effectively
-- never changes, so it is constrained in the database rather than only in code.
--
-- A seeded lookup table rather than an enum: the composite foreign key below
-- enforces that a 'weapon' row carries a weapon name, which an enum cannot,
-- and adding a proficiency later is an INSERT rather than an ALTER TYPE.
--
-- Only 'weapon' for now. Classes, professions and the class counters/flags
-- are being reworked, so they are deliberately absent: with nothing defined,
-- the foreign key below makes it impossible to assign one to a member and
-- then have to clean it up when the rework lands. Widen this check and seed
-- the new definitions at that point.
create table if not exists proficiency_defs (
    kind text not null check (kind in ('weapon')),
    name text not null,
    primary key (kind, name)
);

create table if not exists proficiencies (
    member_id uuid not null references members on delete cascade,
    kind      text not null,
    name      text not null,
    level     int  not null default 0 check (level >= 0),
    primary key (member_id, kind, name),
    foreign key (kind, name) references proficiency_defs (kind, name)
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

-- Seed the permitted weapon styles. These match content/proficiencies/combat-styles.md
-- and the [extra.weapons] tables in content/members/_*.md exactly -- 11 in each.
--
-- content/proficiencies/classes.md gates its prestige classes on four names
-- that appear nowhere else, which look like omissions but are not. Confirmed
-- with leadership, none of them belongs here:
--
--   Florentine  the older name for Dual Wield, which is already seeded.
--               Adding it would create two proficiencies for one skill.
--   Glaive      a large red weapon, not a style of its own.
--   Red, Blue   weapon construction categories -- what a weapon IS, per the
--               "red weapons" and "blue weapons" sections of
--               content/resources/gear.md -- not a skill a member holds.
--
-- classes.md uses Florentine and Red as though they were proficiencies, e.g.
-- "Adept in 2 Proficiencies (Red/Florentine/Polearm & 1 other)". That is loose
-- wording in the document rather than a gap in this list.
insert into proficiency_defs (kind, name) values
    ('weapon', 'Single Sword'),
    ('weapon', 'Sword & Board'),
    ('weapon', 'Dual Wield'),
    ('weapon', '2 Handed Weapon'),
    ('weapon', 'Flail'),
    ('weapon', 'Dagger'),
    ('weapon', 'Polearm'),
    ('weapon', 'Spear'),
    ('weapon', 'Rock'),
    ('weapon', 'Javelin'),
    ('weapon', 'Archery')
on conflict (kind, name) do nothing;
