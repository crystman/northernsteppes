-- The member files are gone, so classes, professions and the class
-- counters/flags have nowhere else to live. They were deferred from
-- proficiency_defs while that system was being reworked and the files still
-- held them; with the files deleted, deferring them would mean losing them.
--
-- Defining a name here does not make it awardable: only weapon and profession
-- have commands. The class, counter and flag kinds are seeded so the rework
-- has somewhere to land, and are otherwise unused. See DEFERRED.md at the
-- repository root for what is off and what turning it on would take.

alter table proficiency_defs drop constraint if exists proficiency_defs_kind_check;
alter table proficiency_defs add constraint proficiency_defs_kind_check
    check (kind in ('weapon', 'class', 'profession', 'counter', 'flag'));

insert into proficiency_defs (kind, name) values
    ('class', 'Scout'), ('class', 'Archer'), ('class', 'Ranger'),
    ('class', 'Vanguard'), ('class', 'Soldier'), ('class', 'Berserker'),
    ('class', 'Paladin'), ('class', 'Shaman'), ('class', 'Shieldman'),
    ('class', 'Spearman'), ('class', 'Thief'), ('class', 'Assassin'),
    ('class', 'Rogue'), ('class', 'Swashbuckler'),

    ('counter', 'Light_Armor'), ('counter', 'Armor'),

    ('flag', 'Steal_10'), ('flag', 'Steal_20'), ('flag', 'Steal_30'),
    ('flag', 'Look_Part'),

    ('profession', 'Armorsmith'), ('profession', 'Entertainer'),
    ('profession', 'Blacksmith'), ('profession', 'Bookbinder'),
    ('profession', 'Brewer'), ('profession', 'Candlemaker'),
    ('profession', 'Clothier'), ('profession', 'Cook'),
    ('profession', 'Fletcher'), ('profession', 'Foamsmith'),
    ('profession', 'Herald'), ('profession', 'Herbalist'),
    ('profession', 'Hunter'), ('profession', 'Leatherworker'),
    ('profession', 'Magistrate'), ('profession', 'Medic'),
    ('profession', 'Merchant'), ('profession', 'Scribe'),
    ('profession', 'Silversmith'), ('profession', 'Weaponsmith'),
    ('profession', 'Woodworker')
on conflict (kind, name) do nothing;
