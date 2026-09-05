"""Tests for the commit-back sync.

Against a fake GitHub rather than the network: what matters is which requests
are made and what is in them, and a real repository could not be asserted
against without writing to it.

The safety properties get the most attention, because the failure modes are
expensive: committing outside content/members, force-pushing over somebody
else's work, or committing when DRY_RUN is set.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from northernsteppes_bot.members import load_all
from northernsteppes_bot.sync import (
    ALLOWED_PREFIX,
    GitHubWriter,
    UnsafePath,
    commit_message,
    sync_once,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"

HEAD = "a" * 40

#: The first sync legitimately rewrites these three even with no data change:
#: the renderer retires `Race` and migrates `Unit` to `Units`. It is a one-off,
#: and it is the mechanism by which race is retired at all -- but it means a
#: "no changes" sync is only a no-op after that migration has landed.
MIGRATED = {"kaigar", "magnus", "meatwolf"}


class FakeGitHub:
    """Records every request, and answers the Git Data API convincingly."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.requests: list[tuple[str, str, dict | None]] = []
        self.ref_updates: list[dict] = []

    def __call__(self, url, method="GET", payload=None):
        self.requests.append((method, url, payload))

        if "/git/ref/heads/" in url:
            return json.dumps({"object": {"sha": HEAD}}).encode()
        if url.endswith("/git/commits/" + HEAD):
            return json.dumps({"tree": {"sha": "tree-base"}}).encode()
        if "/contents/content/members" in url:
            return json.dumps([
                {"name": f"_{slug}.md", "type": "file", "sha": f"blob-{slug}"}
                for slug in self.files
            ]).encode()
        if "/git/blobs/blob-" in url:
            slug = url.rsplit("blob-", 1)[1]
            return json.dumps({
                "content": base64.b64encode(
                    self.files[slug].encode()
                ).decode()
            }).encode()
        if url.endswith("/git/blobs"):
            return json.dumps({"sha": "new-blob"}).encode()
        if url.endswith("/git/trees"):
            return json.dumps({"sha": "new-tree"}).encode()
        if url.endswith("/git/commits"):
            return json.dumps({"sha": "new-commit"}).encode()
        if "/git/refs/heads/" in url:
            self.ref_updates.append(payload)
            return b"{}"
        raise AssertionError(f"unexpected request: {method} {url}")


@pytest.fixture
def repo_files() -> dict[str, str]:
    return {
        p.stem.lstrip("_"): p.read_text(encoding="utf-8")
        for p in MEMBERS_DIR.glob("_*.md") if p.name != "_index.md"
    }


@pytest.fixture
def sheets():
    return load_all(MEMBERS_DIR)


def writer_for(fake) -> GitHubWriter:
    return GitHubWriter("o/r", "main", "token", opener=fake)


# --- nothing to do ---------------------------------------------------------

def test_first_sync_migrates_the_retired_keys(repo_files, sheets):
    """With no data change at all, the first sync still rewrites the members
    carrying Race or Unit. That is how race gets retired."""
    fake = FakeGitHub(repo_files)
    result = sync_once(writer_for(fake), sheets, dry_run=False)
    assert set(result.changed) == MIGRATED


def test_a_second_sync_changes_nothing(repo_files, sheets):
    """Once the migration has landed, an unchanged sync must be a true no-op
    -- otherwise it would commit on every tick, forever."""
    fake = FakeGitHub(repo_files)
    first = sync_once(writer_for(fake), sheets, dry_run=False)

    settled = dict(repo_files) | first.changed
    again = sync_once(writer_for(FakeGitHub(settled)), sheets, dry_run=False)

    assert again.changed == {}
    assert again.commit_sha is None
    assert again.summary() == "nothing to sync"


# --- dry run ---------------------------------------------------------------

def test_dry_run_never_writes(repo_files, sheets):
    """DRY_RUN is one of the two switches guarding the repository."""
    sheets[0].dues_years[2026] = True
    fake = FakeGitHub(repo_files)
    result = sync_once(writer_for(fake), sheets, dry_run=True)

    assert result.changed, "expected a rendered difference"
    assert result.commit_sha is None
    assert fake.ref_updates == []
    assert not any(m != "GET" for m, _, _ in fake.requests), (
        "a dry run made a mutating request"
    )


def test_dry_run_says_what_it_would_do(repo_files, sheets):
    sheets[0].dues_years[2026] = True
    fake = FakeGitHub(repo_files)
    assert "would update" in sync_once(
        writer_for(fake), sheets, dry_run=True
    ).summary()


# --- committing ------------------------------------------------------------

def test_a_change_produces_one_commit(repo_files, sheets):
    """A gathering's worth of edits should be one commit, not one per member."""
    for sheet in sheets[:5]:
        sheet.dues_years[2026] = True
    fake = FakeGitHub(repo_files)
    result = sync_once(writer_for(fake), sheets, dry_run=False)

    assert set(result.changed) == {s.slug for s in sheets[:5]} | MIGRATED
    commits = [u for m, u, _ in fake.requests
               if m == "POST" and u.endswith("/git/commits")]
    assert len(commits) == 1, f"expected one commit, made {len(commits)}"
    assert result.commit_sha == "new-commit"


def test_the_ref_update_is_not_forced(repo_files, sheets):
    """A rejected update means somebody else pushed. Forcing would discard
    their work; the caller should re-read instead."""
    sheets[0].dues_years[2026] = True
    fake = FakeGitHub(repo_files)
    sync_once(writer_for(fake), sheets, dry_run=False)
    assert fake.ref_updates == [{"sha": "new-commit", "force": False}]


def test_the_commit_builds_on_the_current_head(repo_files, sheets):
    sheets[0].dues_years[2026] = True
    fake = FakeGitHub(repo_files)
    sync_once(writer_for(fake), sheets, dry_run=False)
    commit = next(p for m, u, p in fake.requests
                  if m == "POST" and u.endswith("/git/commits"))
    assert commit["parents"] == [HEAD]


def test_only_changed_files_are_written(repo_files, sheets):
    sheets[0].dues_years[2026] = True
    fake = FakeGitHub(repo_files)
    sync_once(writer_for(fake), sheets, dry_run=False)
    tree = next(p for m, u, p in fake.requests
                if m == "POST" and u.endswith("/git/trees"))
    written = {e["path"].rsplit("_", 1)[1].removesuffix(".md")
               for e in tree["tree"]}
    assert written == {sheets[0].slug} | MIGRATED, (
        "only changed members should be rewritten"
    )


# --- safety ----------------------------------------------------------------

def test_paths_outside_content_members_are_refused():
    """A bug producing a stray path would be committing to parts of the site
    nobody asked it to touch."""
    fake = FakeGitHub({})
    with pytest.raises(UnsafePath):
        writer_for(fake).commit(HEAD, {"config.toml": "x"}, "msg")


def test_traversal_is_refused():
    fake = FakeGitHub({})
    with pytest.raises(UnsafePath):
        writer_for(fake).commit(
            HEAD, {f"{ALLOWED_PREFIX}../../config.toml": "x"}, "msg"
        )


def test_nothing_is_written_when_a_path_is_refused():
    """The check runs before the ref update, so a bad batch commits nothing."""
    fake = FakeGitHub({})
    with pytest.raises(UnsafePath):
        writer_for(fake).commit(HEAD, {"evil.md": "x"}, "msg")
    assert fake.ref_updates == []


# --- the message -----------------------------------------------------------

def test_commit_message_names_the_members(sheets):
    by_slug = {s.slug: s for s in sheets}
    message = commit_message({"lamp": "", "goose": ""}, by_slug)
    assert "2 member records" in message
    assert "- Lamp" in message and "- Goose" in message


def test_commit_message_is_singular_for_one(sheets):
    by_slug = {s.slug: s for s in sheets}
    assert "1 member record\n" in commit_message({"lamp": ""}, by_slug)


def test_commit_message_records_the_origin(sheets):
    by_slug = {s.slug: s for s in sheets}
    assert "Recorded via Discord." in commit_message({"lamp": ""}, by_slug)
