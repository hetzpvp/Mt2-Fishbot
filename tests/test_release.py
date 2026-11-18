"""tools/release.py: the version edits it makes to the real project files
(applied in memory, nothing on disk changes)."""

import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("release", os.path.join(ROOT, "tools", "release.py"))
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

V = "9.8.7"


def test_version_py_sets_version_and_appends_history_once():
    text = release.read(release.VERSION_FILE)
    once = release.set_version_py(text, V, "Something new")
    assert release.current_version(once) == V
    assert once.rstrip().endswith(f"# {V} - Something new")
    assert release.set_version_py(once, V, "Something new") == once
    # older history untouched
    assert "# 2.0.1 - " in once


def test_spec_names_both_platforms():
    win = release.set_spec_name(release.read(release.WIN_SPEC), V, linux=False)
    lin = release.set_spec_name(release.read(release.LINUX_SPEC), V, linux=True)
    assert f"name='Fishing Puzzle Player v{V}'," in win
    assert f"name='Fishing Puzzle Player v{V} (linux)'," in lin
    # the Windows pattern must not eat the Linux name and vice versa
    with pytest.raises(ValueError):
        release.set_spec_name(release.read(release.LINUX_SPEC), V, linux=False)


def test_readme_title_links_and_history():
    text = release.set_readme(release.read(release.README), V, "New stuff", "https://vt.example/x")
    assert f"# MT2 Fishing Bot — v{V}" in text
    assert f"[Download v{V}](https://github.com/{release.REPO}/releases/latest)" in text
    line = next(l for l in text.splitlines() if l.startswith("**Download v"))
    assert release.asset_url(V, release.win_asset(V)) in line
    assert release.asset_url(V, release.linux_asset(V)) in line
    assert "https://vt.example/x" in line
    assert "mediafire" not in line.lower()
    hist = text.split("## Version history\n\n", 1)[1]
    assert hist.startswith(f"- **{V}** — New stuff\n")
    # idempotent, and re-running with new notes replaces the entry
    assert release.set_readme(text, V, "New stuff", "https://vt.example/x") == text
    again = release.set_readme(text, V, "Better wording", None)
    assert len(re.findall(rf"^- \*\*{re.escape(V)}\*\*", again, re.M)) == 1
    assert "Better wording" in again


def test_readme_requires_notes_for_new_version():
    with pytest.raises(ValueError):
        release.set_readme(release.read(release.README), V, None)


def test_asset_names_are_url_safe():
    for name in (release.win_asset(V), release.linux_asset(V)):
        assert re.fullmatch(r"[A-Za-z0-9.\-]+", name)
    assert release.tag_name(V) == f"Mt2-Fishbot-v{V}"
