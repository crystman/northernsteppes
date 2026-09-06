"""Every proficiency the database can hold must be written up on the site.

The member page links each weapon style, profession and class to the section
that explains it, building the URL from the name: "Sword & Board" becomes
``combat-styles/#sword-board``. That only works while the two agree, and
nothing else checks it -- a profession seeded in a migration with no section
on the site produces a link that 200s and lands nowhere, which is invisible
until somebody clicks it.

This reads the markdown rather than a built site, so it needs no Zola. The
anchors Zola generates are the slugified heading text, which is what
``slugify`` reproduces here and in the template's JavaScript.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFICIENCIES = REPO_ROOT / "content" / "proficiencies"
MIGRATIONS = REPO_ROOT / "bot" / "migrations"

#: Which page each kind is written up on, matching PAGES in member_live.html.
PAGE_FOR_KIND = {
    "weapon": "combat-styles",
    "profession": "non-combat-classes",
    "class": "classes",
}

pytestmark = pytest.mark.skipif(
    not PROFICIENCIES.is_dir(),
    reason="run from a full checkout; the bot alone has no content/",
)


def slugify(name: str) -> str:
    """The anchor Zola gives a heading, and what the template builds."""
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def anchors(page: str) -> set[str]:
    text = (PROFICIENCIES / f"{page}.md").read_text(encoding="utf-8")
    return {slugify(h) for h in re.findall(r"^#+\s+(.*?)\s*$", text, re.M)}


def seeded() -> dict[str, list[str]]:
    """Every (kind, name) the migrations define, as the database will hold it."""
    sql = "".join(
        p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql"))
    )
    found: dict[str, list[str]] = {}
    for kind, name in re.findall(
        r"\('(weapon|class|profession|counter|flag)',\s*'([^']+)'\)", sql
    ):
        found.setdefault(kind, []).append(name)
    return found


@pytest.mark.parametrize("kind", sorted(PAGE_FOR_KIND))
def test_every_seeded_name_has_a_section_to_link_to(kind):
    page = PAGE_FOR_KIND[kind]
    available = anchors(page)
    names = sorted(set(seeded().get(kind, [])))
    assert names, f"expected the migrations to seed some {kind} names"

    missing = [n for n in names if slugify(n) not in available]
    assert not missing, (
        f"{kind} names with no section on {page}.md: {missing}. "
        "The member page links to one, so these would land nowhere."
    )


def test_the_awkward_names_resolve():
    """Named rather than left to the sweep above, because these are the ones
    where the slug and the heading are not obviously the same string."""
    styles = anchors("combat-styles")
    assert slugify("Sword & Board") == "sword-board"
    assert "sword-board" in styles
    # The database says "2 Handed Weapon"; the heading says "2-Handed Weapon".
    assert slugify("2 Handed Weapon") == slugify("2-Handed Weapon")
    assert "2-handed-weapon" in styles


@pytest.mark.parametrize("rank", ["Peasant", "Savage", "Harbinger"])
def test_every_earned_rank_has_a_section(rank):
    """The member page links the rank line to it."""
    assert slugify(rank) in anchors("index")


def test_unranked_has_no_section_of_its_own():
    """Which is why the template leaves it as plain text. If somebody adds a
    section for it, the link should be turned on rather than this deleted."""
    assert slugify("Unranked") not in anchors("index")


@pytest.mark.parametrize("ladder", ["Scout", "Soldier", "Thief"])
def test_every_class_ladder_has_a_section(ladder):
    """Not linked today -- the class system is deferred, see DEFERRED.md --
    but kept so the sections stay in step and re-enabling is a template
    change rather than an investigation."""
    assert slugify(ladder) in anchors("classes")


MEMBER_TEMPLATE = REPO_ROOT / "templates" / "member_live.html"


def test_the_member_page_does_not_render_class_ladders():
    """Deferred with the rest of the class system. Asserted rather than left
    to reading, because the API still sends m.classes and re-adding a section
    for it is a two-line change somebody could make without meaning to."""
    template = MEMBER_TEMPLATE.read_text(encoding="utf-8")
    assert "member-classes" not in template, (
        "the class ladder section is back on the member page; if that is "
        "intended, update DEFERRED.md and delete this test"
    )
