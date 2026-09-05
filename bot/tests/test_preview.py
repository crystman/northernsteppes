"""Tests for the preview command.

The database path is thin glue over `overlay` and `render_changes`, both
covered elsewhere. What is worth testing here is the parts that decide whether
anything gets written, and the message when there is no database -- because
this command is most likely to be run by somebody who has not set one up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from northernsteppes_bot.preview import diff_for, main

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"


def test_diff_is_a_readable_unified_diff():
    out = diff_for("lamp", "a\nb\n", "a\nc\n")
    assert "a/content/members/_lamp.md" in out
    assert "-b" in out and "+c" in out


def test_no_diff_for_identical_content():
    assert diff_for("lamp", "same\n", "same\n") == ""


def test_missing_database_url_explains_railway_run(monkeypatch, capsys):
    """The likely first run: no DATABASE_URL locally."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert "railway run" in str(exit_info.value)


def test_missing_members_directory_is_reported(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    with pytest.raises(SystemExit) as exit_info:
        main(["--members-dir", "does/not/exist"])
    assert "no member files" in str(exit_info.value)


def test_write_is_off_by_default():
    """Reporting is the default; writing to the working tree is opt-in."""
    import argparse
    import inspect
    from northernsteppes_bot import preview

    source = inspect.getsource(preview.main)
    assert '"--write", action="store_true"' in source, (
        "--write must be a flag defaulting to off"
    )
