"""Assert the Python rank rules agree with the site's Tera macros.

The site keeps its own implementation in ``templates/ranks.html`` so it never
depends on the bot. That means the rules exist twice, which is only safe if
something checks they still agree. This builds the real site and compares the
rendered rank for every member against the Python result.

Skipped when Zola is unavailable, so the unit tests still run on a machine
without it. CI runs it with Zola present, where the skip would be a silent
hole -- pass ``--require-zola`` there to turn the skip into a failure.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from northernsteppes_bot.members import load_all
from northernsteppes_bot.ranks import (
    RANK_NAMES,
    SCOUT_NAMES,
    SOLDIER_NAMES,
    THIEF_NAMES,
    rank,
    scout_rank,
    soldier_rank,
    thief_rank,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"

#: <dt><a href=".../proficiencies/#ranks">Rank</a></dt><dd>Harbinger</dd>
_RANK_CELL = re.compile(
    r'proficiencies/#ranks.*?</a></dt>\s*<dd[^>]*>(.*?)</dd>', re.S
)


def _definition(html: str, anchor: str) -> str | None:
    """Pull the <dd> that follows the <dt> linking to ``anchor``."""
    # <dd[^>]*> rather than <dd>: the member template carries data-live
    # attributes for the live-status script, and this test is about the
    # rendered value, not the markup around it.
    match = re.search(
        re.escape(anchor) + r'".*?</a></dt>\s*<dd[^>]*>(.*?)</dd>', html, re.S
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yaml"


def required_zola_version() -> str:
    """The Zola version CI builds with, read from the workflow.

    The deploy action's tags mirror the bundled Zola version, so the pin in
    publish.yaml is the single source of truth. Deriving it here means a local
    build that would not match CI fails the test instead of silently comparing
    against different output.
    """
    match = re.search(
        r"shalzz/zola-deploy-action@v(\d+\.\d+\.\d+)",
        WORKFLOW.read_text(encoding="utf-8"),
    )
    if match is None:
        pytest.fail(f"could not read the pinned zola version from {WORKFLOW}")
    return match.group(1)


def _version_of(binary: str) -> str | None:
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", out.stdout)
    return match.group(1) if match else None


def _find_zola() -> tuple[str | None, list[str]]:
    """Locate a zola binary matching the pinned version.

    Returns the path plus a list of near misses, so the skip message can say
    "you have 0.23.4 but CI uses 0.22.1" rather than just "not found" -- the
    likely case, since `winget install getzola.zola` gives a version that
    cannot build this site at all.
    """
    wanted = required_zola_version()
    rejected: list[str] = []

    candidates = [
        Path.home() / "tools" / f"zola-{wanted}" / "zola.exe",
        Path.home() / "tools" / f"zola-{wanted}" / "zola",
    ]
    found = shutil.which("zola")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if not Path(candidate).exists():
            continue
        version = _version_of(str(candidate))
        if version == wanted:
            return str(candidate), rejected
        if version:
            rejected.append(f"{candidate} is {version}")
    return None, rejected


@pytest.fixture(scope="module")
def built_site(request) -> Path:
    zola, rejected = _find_zola()
    if zola is None:
        detail = f"need zola {required_zola_version()} (the version CI pins)"
        if rejected:
            detail += "; found " + ", ".join(rejected)
        detail += ". See readme.md 'Local Development'."
        if request.config.getoption("--require-zola"):
            pytest.fail(detail)
        pytest.skip(detail)

    out = Path(tempfile.mkdtemp(prefix="ns-parity-"))
    result = subprocess.run(
        [zola, "build", "-o", str(out), "--force"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"zola build failed:\n{result.stdout}\n{result.stderr}")

    yield out
    shutil.rmtree(out, ignore_errors=True)


def _member_pages(built_site: Path) -> dict[str, str]:
    pages = {}
    for path in (built_site / "members").rglob("index.html"):
        if path.parent.name != "members":
            pages[path.parent.name] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    return pages


def test_every_member_file_is_rendered(built_site):
    """Guards against the comparison below passing vacuously."""
    sheets = load_all(MEMBERS_DIR)
    assert sheets, "no member files found"
    pages = _member_pages(built_site)
    assert {s.slug for s in sheets} == set(pages), (
        "member files and rendered pages disagree: "
        f"{ {s.slug for s in sheets} ^ set(pages) }"
    )


def test_overall_rank_matches_site(built_site):
    pages = _member_pages(built_site)
    mismatches = []
    for s in load_all(MEMBERS_DIR):
        rendered = _definition(pages[s.slug], "proficiencies/#ranks")
        expected = RANK_NAMES[rank(s)]
        if rendered != expected:
            mismatches.append(f"{s.slug}: site={rendered!r} python={expected!r}")
    assert not mismatches, "rank rules have drifted:\n" + "\n".join(mismatches)


@pytest.mark.parametrize(
    "anchor,fn,names",
    [
        ("proficiencies/classes/#scout", scout_rank, SCOUT_NAMES),
        ("proficiencies/classes/#soldier", soldier_rank, SOLDIER_NAMES),
        ("proficiencies/classes/#thief", thief_rank, THIEF_NAMES),
    ],
)
def test_class_ladders_match_site(built_site, anchor, fn, names):
    pages = _member_pages(built_site)
    mismatches = []
    for s in load_all(MEMBERS_DIR):
        rendered = _definition(pages[s.slug], anchor)
        expected = names[fn(s)]
        if rendered != expected:
            mismatches.append(f"{s.slug}: site={rendered!r} python={expected!r}")
    assert not mismatches, f"{anchor} has drifted:\n" + "\n".join(mismatches)
