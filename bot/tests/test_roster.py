"""Tests for member lookup.

The behaviour that matters most here is what happens when a query is
ambiguous. Silently picking the first match is how a proficiency ends up
awarded to the wrong person, so search() returns every match and lets the
caller ask.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from northernsteppes_bot.roster import MemberDirectory, default_members_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"


@pytest.fixture(scope="module")
def directory() -> MemberDirectory:
    d = MemberDirectory(MEMBERS_DIR)
    d.load()
    return d


def test_default_path_points_at_the_real_member_files():
    assert default_members_dir() == MEMBERS_DIR
    assert default_members_dir().is_dir()


def test_loads_every_member(directory):
    slugs = {s.slug for s in directory.all()}
    on_disk = {
        p.stem.lstrip("_") for p in MEMBERS_DIR.glob("_*.md")
        if p.name != "_index.md"
    }
    assert slugs == on_disk


def test_section_index_is_not_a_member(directory):
    assert "index" not in {s.slug for s in directory.all()}


def test_by_slug_is_case_insensitive(directory):
    assert directory.by_slug("LAMP") is not None
    assert directory.by_slug("lamp").slug == "lamp"


def test_by_slug_returns_none_for_unknown(directory):
    assert directory.by_slug("nobody-here") is None


def test_search_finds_by_display_name(directory):
    found = directory.search("Magnus Broadaxe")
    assert len(found) == 1 and found[0].slug == "magnus"


def test_search_prefers_an_exact_match_over_substrings(directory):
    """'kam' is also a substring of nothing else today, but the rule matters:
    an exact slug must never be buried among partial matches."""
    found = directory.search("kam")
    assert len(found) == 1 and found[0].slug == "kam"


def test_search_returns_all_partial_matches(directory):
    found = directory.search("a")
    assert len(found) > 1, "expected several members to contain 'a'"


def test_search_is_empty_for_no_match(directory):
    assert directory.search("zzzznope") == []


def test_search_ignores_blank_queries(directory):
    assert directory.search("   ") == []


def test_choices_respects_discords_limit(directory):
    assert len(directory.choices("")) <= 25


def test_choices_filters_on_the_query(directory):
    for s in directory.choices("lam"):
        assert "lam" in s.slug.lower() or "lam" in s.display_name.lower()


# These use tempfile rather than pytest's tmp_path fixture. tmp_path keeps a
# rotating pytest-of-<user> directory that fails with a PermissionError if a
# stale one is left behind by an earlier run, which has nothing to do with
# what is being tested.

def test_cache_reloads_after_ttl():
    src = (MEMBERS_DIR / "_lamp.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "_lamp.md").write_text(src, encoding="utf-8")
        d = MemberDirectory(tmp, ttl_seconds=0)
        assert len(d.all()) == 1

        (tmp / "_second.md").write_text(src, encoding="utf-8")
        assert len(d.all()) == 2, "expected a reload once the TTL had expired"


def test_cache_holds_within_ttl():
    src = (MEMBERS_DIR / "_lamp.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "_lamp.md").write_text(src, encoding="utf-8")
        d = MemberDirectory(tmp, ttl_seconds=3600)
        assert len(d.all()) == 1

        (tmp / "_second.md").write_text(src, encoding="utf-8")
        assert len(d.all()) == 1, "should still be serving the cached list"


# --- background refresh ----------------------------------------------------

def test_auto_reload_off_keeps_serving_the_cached_list():
    """The bot disables auto-reload and refreshes on a background task, so no
    autocomplete keystroke ever pays for re-parsing 21 files."""
    src = (MEMBERS_DIR / "_lamp.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "_lamp.md").write_text(src, encoding="utf-8")
        d = MemberDirectory(tmp, ttl_seconds=0, auto_reload=False)
        assert len(d.all()) == 1

        (tmp / "_second.md").write_text(src, encoding="utf-8")
        assert d.is_stale() is True, "should still report itself stale"
        assert len(d.all()) == 1, "but must not reload on the request path"

        d.load()
        assert len(d.all()) == 2, "an explicit refresh still works"


def test_first_call_loads_even_with_auto_reload_off():
    """Otherwise every command would answer from an empty roster until the
    first background tick."""
    src = (MEMBERS_DIR / "_lamp.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "_lamp.md").write_text(src, encoding="utf-8")
        d = MemberDirectory(tmp, auto_reload=False)
        assert len(d.all()) == 1
