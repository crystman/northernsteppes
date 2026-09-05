"""Tests for writing member files back.

The two properties that matter are that a no-op change produces a byte-identical
file, and that a real change produces a minimal diff. Both are checked against
all 21 real member files rather than fixtures, because the risk being guarded
against is damage to those exact documents.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from northernsteppes_bot.members import load_all, parse_member, split_frontmatter
from northernsteppes_bot.renderer import (
    RETIRED_KEYS,
    RenderError,
    render_changes,
    render_member,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"


def member_files() -> list[Path]:
    return sorted(p for p in MEMBERS_DIR.glob("_*.md") if p.name != "_index.md")


def diff_lines(before: str, after: str) -> list[str]:
    return [
        line for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


@pytest.fixture(scope="module")
def sheets():
    return {s.slug: s for s in load_all(MEMBERS_DIR)}


@pytest.fixture(scope="module")
def files():
    return {
        p.stem.lstrip("_"): p.read_text(encoding="utf-8") for p in member_files()
    }


# --- the round trip --------------------------------------------------------

@pytest.mark.parametrize("path", member_files(), ids=lambda p: p.stem)
def test_unchanged_input_produces_a_minimal_diff(path):
    """Re-rendering a member with their own current values must not reformat
    the file. The only permitted changes are the retired keys."""
    before = path.read_text(encoding="utf-8")
    after = render_member(before, parse_member(path))

    for line in diff_lines(before, after):
        assert any(key in line for key in RETIRED_KEYS), (
            f"{path.name}: unexpected change to a line this renderer does not "
            f"own: {line!r}"
        )


def test_files_without_retired_keys_round_trip_byte_identically(files, sheets):
    """For most members the render is a true no-op."""
    untouched = [
        slug for slug, text in files.items()
        if not any(key in text for key in RETIRED_KEYS)
    ]
    assert untouched, "expected some members to have no retired keys"
    for slug in untouched:
        assert render_member(files[slug], sheets[slug]) == files[slug], (
            f"{slug} changed on a no-op render"
        )


def test_rendering_is_idempotent(files, sheets):
    """Running the sync twice must not keep producing commits."""
    for slug, text in files.items():
        once = render_member(text, sheets[slug])
        twice = render_member(once, sheets[slug])
        assert once == twice, f"{slug} is not stable under a second render"


def test_render_changes_reports_only_real_changes(files, sheets):
    changed = render_changes(files, sheets)
    for slug in changed:
        assert any(key in files[slug] for key in RETIRED_KEYS), (
            f"{slug} reported as changed but has no retired keys"
        )


# --- what must survive -----------------------------------------------------

@pytest.mark.parametrize("path", member_files(), ids=lambda p: p.stem)
def test_deferred_tables_survive(path):
    """Classes and professions are deferred from the database and still
    hand-edited. A renderer that wrote 'everything the database knows' would
    delete them; this is the test that would catch that."""
    before = path.read_text(encoding="utf-8")
    after = render_member(before, parse_member(path))

    doc_before, _ = split_frontmatter(before)
    doc_after, _ = split_frontmatter(after)
    for table in ("classes", "professions"):
        if table in doc_before.get("extra", {}):
            assert table in doc_after["extra"], f"{path.name}: lost [extra.{table}]"
            assert (
                dict(doc_after["extra"][table]) == dict(doc_before["extra"][table])
            ), f"{path.name}: [extra.{table}] was modified"


@pytest.mark.parametrize("path", member_files(), ids=lambda p: p.stem)
def test_body_and_date_survive(path):
    before = path.read_text(encoding="utf-8")
    after = render_member(before, parse_member(path))

    assert split_frontmatter(before)[1] == split_frontmatter(after)[1]
    doc_before, _ = split_frontmatter(before)
    doc_after, _ = split_frontmatter(after)
    if "date" in doc_before:
        assert str(doc_after["date"]) == str(doc_before["date"])


def test_retired_keys_are_removed():
    path = MEMBERS_DIR / "_kaigar.md"
    before = path.read_text(encoding="utf-8")
    assert "Race" in before, "fixture assumption: kaigar has a Race"

    after = render_member(before, parse_member(path))
    assert "Race" not in split_frontmatter(after)[0]["extra"]


# --- real edits ------------------------------------------------------------

def test_recording_dues_adds_one_line(files, sheets):
    slug = "lamp"
    sheet = sheets[slug]
    sheet.dues_years[2026] = True

    after = render_member(files[slug], sheet)
    added = [l for l in diff_lines(files[slug], after) if l.startswith("+")]
    assert len(added) == 1, f"expected one added line, got {added}"
    assert "2026" in added[0]

    del sheet.dues_years[2026]  # module-scoped fixture


def test_awarding_a_style_changes_one_line(files, sheets):
    slug = "lamp"
    sheet = sheets[slug]
    original = sheet.weapons["Archery"]
    sheet.weapons["Archery"] = 3

    after = render_member(files[slug], sheet)
    changes = diff_lines(files[slug], after)
    assert len([l for l in changes if l.startswith("+")]) == 1
    assert any("Archery" in l for l in changes)

    sheet.weapons["Archery"] = original


def test_a_removed_style_is_dropped(files, sheets):
    slug = "lamp"
    sheet = sheets[slug]
    removed = sheet.weapons.pop("Flail")

    after = render_member(files[slug], sheet)
    assert "Flail" not in split_frontmatter(after)[0]["extra"]["weapons"]

    sheet.weapons["Flail"] = removed


def test_unpaid_dues_years_are_not_written(files, sheets):
    """A row means paid, so a year recorded as false has no meaning."""
    slug = "lamp"
    sheet = sheets[slug]
    sheet.dues_years[2019] = False

    after = render_member(files[slug], sheet)
    assert "2019" not in str(split_frontmatter(after)[0]["extra"]["dues"])

    del sheet.dues_years[2019]


# --- failure modes ---------------------------------------------------------

def test_missing_extra_table_is_an_error():
    from northernsteppes_bot.ranks import MemberSheet
    with pytest.raises(RenderError):
        render_member("+++\ntitle = \"X\"\n+++\n", MemberSheet(slug="x"))


def test_unparseable_file_is_an_error():
    from northernsteppes_bot.ranks import MemberSheet
    with pytest.raises(ValueError):
        render_member("no frontmatter here", MemberSheet(slug="x"))


def test_members_without_a_file_are_skipped(files, sheets):
    """Creating files is a wider permission than editing them."""
    from northernsteppes_bot.ranks import MemberSheet
    extended = dict(sheets)
    extended["brand-new"] = MemberSheet(slug="brand-new", display_name="New")
    assert "brand-new" not in render_changes(files, extended)


def test_unit_is_migrated_to_units_not_dropped():
    """The singular key is retired, but its value must move to Units. Dropping
    it would silently lose which unit a member fights with."""
    path = MEMBERS_DIR / "_kaigar.md"
    before = path.read_text(encoding="utf-8")
    assert 'Unit =' in before, "fixture assumption: kaigar has a Unit"

    after = render_member(before, parse_member(path))
    extra = split_frontmatter(after)[0]["extra"]
    assert "Unit" not in extra
    assert list(extra["Units"]) == ["CoWS"]


def test_units_absent_for_members_without_one():
    """No empty arrays on the members who never had a unit -- that would be
    diff noise on nineteen files."""
    path = MEMBERS_DIR / "_lamp.md"
    after = render_member(path.read_text(encoding="utf-8"), parse_member(path))
    assert "Units" not in split_frontmatter(after)[0]["extra"]


def test_units_round_trip_once_migrated():
    """Second sync must not rewrite an already-migrated file."""
    path = MEMBERS_DIR / "_kaigar.md"
    once = render_member(path.read_text(encoding="utf-8"), parse_member(path))
    twice = render_member(once, parse_member(path))
    assert once == twice
