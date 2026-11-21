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


def test_entry_script_calls_freeze_support_before_heavy_imports():
    # A frozen build without it re-runs the whole GUI for every multiprocessing
    # helper (Numba's lock starts a resource tracker under Python 3.14's
    # default "forkserver" start method): copies pile up and outlive the window.
    with open(os.path.join(ROOT, "src", "fishing_bot.py"), encoding="utf-8") as f:
        src = f.read()
    call = src.index("multiprocessing.freeze_support()")
    for heavy in ("import diag", "import cv2", "import numpy", "from qt_gui import"):
        assert call < src.index(heavy), heavy


def test_processes_of_finds_this_process():
    import sys
    if not os.path.isdir("/proc"):
        pytest.skip("needs /proc")
    found = release.processes_of(sys.executable)
    assert os.getpid() in found
    assert release.processes_of(os.path.join(ROOT, "no-such-program")) == {}


NOTES = """## What's new
- **Golden Tuna** dialog handled
- Dead fish follow
  the living one

## Download
- not a highlight
"""


def test_announce_highlights_come_from_whats_new():
    assert release.notes_highlights(NOTES) == ["**Golden Tuna** dialog handled",
                                               "Dead fish follow the living one"]
    with pytest.raises(ValueError):
        release.notes_highlights("## Download\n- x\n")


@pytest.mark.parametrize("kind", ["discord", "elitepvpers"])
def test_announcements_fill_every_placeholder(kind):
    hl = release.notes_highlights(NOTES)
    text = release.render_announcement(kind, V, hl, "21/11/2026")
    assert "{{" not in text and V in text and "21/11/2026" in text
    assert release.asset_url(V, release.win_asset(V)) in text
    assert release.asset_url(V, release.linux_asset(V)) in text
    assert "Golden Tuna" in text and "coming soon" in text
    with_links = release.render_announcement(kind, V, hl, "21/11/2026",
                                             mediafire="https://mf.example/x", virustotal="https://vt.example/y")
    assert "https://mf.example/x" in with_links and "https://vt.example/y" in with_links
    if kind == "elitepvpers":
        assert "**" not in text and "[*]Golden Tuna dialog handled" in text


def _png_with_text(path, key=None, value=None):
    """A 1x1 PNG, with a tEXt chunk key=value before IEND when given."""
    import struct
    import zlib

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    if key is not None:
        data += chunk(b"tEXt", key.encode() + b"\0" + value.encode())
    data += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


def test_screenshot_version_stamp(tmp_path):
    stamped, plain, other = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"
    _png_with_text(stamped, release.SCREENSHOT_KEY, V)
    _png_with_text(plain)
    _png_with_text(other, "Software", "Qt")
    assert release.screenshot_version(str(stamped)) == V
    assert release.screenshot_version(str(plain)) is None
    assert release.screenshot_version(str(other)) is None
    assert release.screenshot_version(str(tmp_path / "missing.png")) is None


def test_readme_shows_every_screenshot_the_release_renews():
    readme = release.read(release.README)
    for name in release.SCREENSHOTS:
        assert f"docs/{name}" in readme, name


def test_screenshot_tool_renders_the_readme_pictures(tmp_path):
    """tools/readme_screenshots.py, offscreen, into a temporary folder."""
    import subprocess
    import sys
    pytest.importorskip("PySide6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("FISHBOT_CONFIG_DIR", None)          # the tool sets its own
    res = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readme_screenshots.py"), str(tmp_path)],
                         cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180)
    assert res.returncode == 0, res.stdout + res.stderr
    version = release.current_version()
    for name in release.SCREENSHOTS:
        path = tmp_path / name
        assert path.exists(), name
        assert release.screenshot_version(str(path)) == version, name
    assert not (tmp_path / "bot_config.json").exists()
