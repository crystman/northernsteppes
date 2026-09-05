"""Tests for where member files are read from.

The GitHub source is tested against a fake fetcher rather than the network, so
the suite stays offline and deterministic. What is worth checking is the URL
construction, the filtering, and that one listing call is made rather than one
per file -- unauthenticated GitHub allows 60 API calls an hour, and a refresh
that spent 21 of them would exhaust that in three refreshes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northernsteppes_bot.roster import MemberDirectory
from northernsteppes_bot.sources import (
    DEFAULT_REPO,
    GitHubSource,
    LocalSource,
    choose_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"

SAMPLE = """+++
title = "Fake"

[extra]
Waiver = true

[extra.dues]
2026 = true

[extra.weapons]
"Single Sword" = 1
+++
"""


class FakeGitHub:
    """Records every URL requested, so call counts can be asserted."""

    def __init__(self, names):
        self.names = names
        self.calls: list[str] = []

    def __call__(self, url: str, accept: str = "") -> bytes:
        self.calls.append(url)
        if "api.github.com" in url:
            return json.dumps(
                [{"name": n, "type": "file"} for n in self.names]
            ).encode()
        return SAMPLE.encode()


# --- local -----------------------------------------------------------------

def test_local_source_reads_member_files():
    files = LocalSource(MEMBERS_DIR).fetch()
    assert "_lamp.md" in files
    assert files["_lamp.md"].startswith("+++")


def test_local_source_skips_the_section_index():
    """_index.md defines the section, not a member."""
    assert "_index.md" not in LocalSource(MEMBERS_DIR).fetch()


def test_local_source_describes_its_path():
    assert "members" in LocalSource(MEMBERS_DIR).describe()


# --- github ----------------------------------------------------------------

def test_github_source_builds_the_expected_urls():
    source = GitHubSource("owner/repo", "somebranch")
    assert source.listing_url == (
        "https://api.github.com/repos/owner/repo/contents/"
        "content/members?ref=somebranch"
    )
    assert source.raw_url("_lamp.md") == (
        "https://raw.githubusercontent.com/owner/repo/somebranch/"
        "content/members/_lamp.md"
    )


def test_github_source_fetches_every_member():
    fake = FakeGitHub(["_lamp.md", "_goose.md"])
    files = GitHubSource("o/r", "main", getter=fake).fetch()
    assert set(files) == {"_lamp.md", "_goose.md"}


def test_github_source_lists_once_not_once_per_file():
    """Unauthenticated GitHub allows 60 API calls an hour."""
    fake = FakeGitHub([f"_m{i}.md" for i in range(21)])
    GitHubSource("o/r", "main", getter=fake).fetch()
    api_calls = [u for u in fake.calls if "api.github.com" in u]
    assert len(api_calls) == 1, f"expected one API call, made {len(api_calls)}"


def test_github_source_ignores_the_section_index_and_non_members():
    fake = FakeGitHub(["_lamp.md", "_index.md", "readme.txt", "notes.md"])
    files = GitHubSource("o/r", "main", getter=fake).fetch()
    assert set(files) == {"_lamp.md"}


def test_github_source_defaults_to_the_upstream_repository():
    assert GitHubSource().repo == DEFAULT_REPO


# --- choosing --------------------------------------------------------------

def test_local_wins_when_the_directory_exists():
    """So an uncommitted local change is visible, not masked by the branch."""
    source = choose_source(MEMBERS_DIR, "o/r", "main")
    assert isinstance(source, LocalSource)


def test_github_is_used_when_there_is_no_local_directory():
    source = choose_source(Path("does/not/exist"), "o/r", "main")
    assert isinstance(source, GitHubSource)


def test_github_is_used_when_no_directory_is_given():
    assert isinstance(choose_source(None, "o/r", "main"), GitHubSource)


# --- the directory reads whatever source it is given -----------------------

def test_directory_loads_from_a_github_source():
    """The deploy case: no repository checkout, files over the network."""
    fake = FakeGitHub(["_lamp.md", "_goose.md"])
    directory = MemberDirectory(source=GitHubSource("o/r", "main", getter=fake))
    sheets = directory.load()
    assert {s.slug for s in sheets} == {"lamp", "goose"}
    assert sheets[0].paid_for(2026) is True


def test_directory_still_accepts_a_plain_directory():
    """Every existing caller passes a path; that must keep working."""
    directory = MemberDirectory(MEMBERS_DIR)
    assert len(directory.load()) == 21
