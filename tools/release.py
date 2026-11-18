"""
Release helper for MT2 Fishing Bot. The full flow is in CLAUDE.md
("Release flow"); this script does its mechanical steps.

    python tools/release.py status
        Current version, whether it is already released, the files that carry
        the version and whether they agree, and the build/publish tooling.

    python tools/release.py bump X.Y.Z --notes "one-line changelog"
        Sets the version everywhere: src/version.py (VERSION + history line),
        packaging/build.spec, packaging/build_linux.spec, and README.md
        (title, download links, version history). Safe to run again.

    python tools/release.py build [--skip-windows] [--skip-linux] [--no-smoke]
        Builds both binaries into dist/release-X.Y.Z/, packs the release
        assets (Windows .exe, Linux .tar.gz) and writes SHA256SUMS.txt.
        Headless: the Windows exe is built under Wine, the smoke tests run on
        a private Xvfb display. Takes several minutes.

    python tools/release.py publish --notes-file FILE [--draft]
        Tags HEAD as Mt2-Fishbot-vX.Y.Z, pushes main and the tag, and creates
        the GitHub release with the assets. Refuses if HEAD does not carry the
        version, the tag exists, or an asset is missing.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "hetzpvp/Mt2-Fishbot"
APP = "Fishing Puzzle Player"
VERSION_FILE = os.path.join(ROOT, "src", "version.py")
WIN_SPEC = os.path.join(ROOT, "packaging", "build.spec")
LINUX_SPEC = os.path.join(ROOT, "packaging", "build_linux.spec")
README = os.path.join(ROOT, "README.md")
VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?$")


# ---------------------------------------------------------------- naming

def tag_name(v):
    return f"Mt2-Fishbot-v{v}"


def release_dir(v):
    return os.path.join(ROOT, "dist", f"release-{v}")


def win_asset(v):
    return f"Fishing-Puzzle-Player-v{v}-windows.exe"


def linux_asset(v):
    return f"Fishing-Puzzle-Player-v{v}-linux.tar.gz"


def asset_url(v, name):
    return f"https://github.com/{REPO}/releases/download/{tag_name(v)}/{name}"


# ---------------------------------------------------------------- helpers

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def current_version(text=None):
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text or read(VERSION_FILE), re.M)
    if not m:
        sys.exit("VERSION not found in src/version.py")
    return m.group(1)


def run(cmd, **kw):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def out(cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- version edits

def set_version_py(text, v, notes):
    text = re.sub(r'^VERSION\s*=\s*"[^"]*"', f'VERSION = "{v}"', text, count=1, flags=re.M)
    if notes and not re.search(rf"^# {re.escape(v)} - ", text, re.M):
        text = text.rstrip("\n") + f"\n# {v} - {notes}\n"
    return text


def set_spec_name(text, v, linux):
    suffix = r" \(linux\)" if linux else ""
    new = f"name='{APP} v{v}" + (" (linux)" if linux else "") + "'"
    text, n = re.subn(rf"name='{APP} v[\d.]+{suffix}'", new, text)
    if n != 1:
        raise ValueError(f"expected one exe name in the {'Linux' if linux else 'Windows'} spec, found {n}")
    return text


def set_readme(text, v, notes, virustotal=None):
    # Title
    text, n = re.subn(r"^# MT2 Fishing Bot — v[\d.]+", f"# MT2 Fishing Bot — v{v}", text, count=1, flags=re.M)
    if n != 1:
        raise ValueError("README title '# MT2 Fishing Bot — vX.Y.Z' not found")
    # Short link in the link bar under the intro
    latest = f"https://github.com/{REPO}/releases/latest"
    text, n = re.subn(r"\[Download v[\d.]+\]\([^)]*\)", f"[Download v{v}]({latest})", text, count=1)
    if n != 1:
        raise ValueError("README link '[Download vX.Y.Z](...)' not found")
    # Full download line
    parts = [f"[Windows .exe]({asset_url(v, win_asset(v))})",
             f"[Linux binary]({asset_url(v, linux_asset(v))})"]
    if virustotal:
        parts.append(f"[VirusTotal scan]({virustotal})")
    parts.append(f"[All releases](https://github.com/{REPO}/releases)")
    line = f"**Download v{v}:** " + " · ".join(parts)
    text, n = re.subn(r"^\*\*Download v[^\n]*$", line.replace("\\", r"\\"), text, count=1, flags=re.M)
    if n != 1:
        raise ValueError("README line starting with '**Download v' not found")
    # Version history
    m = re.search(r"^## Version history\n\n", text, re.M)
    if not m:
        raise ValueError("README '## Version history' section not found")
    entry = re.compile(rf"^- \*\*{re.escape(v)}\*\* — .*$", re.M)
    if notes:
        new_entry = f"- **{v}** — {notes}"
        if entry.search(text):
            text = entry.sub(new_entry.replace("\\", r"\\"), text, count=1)
        else:
            text = text[:m.end()] + new_entry + "\n" + text[m.end():]
    elif not entry.search(text):
        raise ValueError(f"README has no version history entry for {v}: pass --notes")
    return text


def cmd_bump(args):
    v = args.version
    if not VERSION_RE.match(v):
        sys.exit(f"not a version: {v} (expected X.Y.Z)")
    vp = read(VERSION_FILE)
    if not args.notes and not re.search(rf"^# {re.escape(v)} - ", vp, re.M):
        sys.exit(f"src/version.py has no history line for {v}: pass --notes")
    edits = {
        VERSION_FILE: set_version_py(vp, v, args.notes),
        WIN_SPEC: set_spec_name(read(WIN_SPEC), v, linux=False),
        LINUX_SPEC: set_spec_name(read(LINUX_SPEC), v, linux=True),
        README: set_readme(read(README), v, args.notes, args.virustotal),
    }
    for path, text in edits.items():
        changed = text != read(path)
        if changed:
            write(path, text)
        print(f"{'updated' if changed else 'unchanged':9} {os.path.relpath(path, ROOT)}")
    print(f"version is now {v}")


# ---------------------------------------------------------------- status

def consistency(v):
    """{file: problem or None}"""
    res = {}
    res["packaging/build.spec"] = None if f"name='{APP} v{v}'" in read(WIN_SPEC) else "exe name not at this version"
    res["packaging/build_linux.spec"] = None if f"name='{APP} v{v} (linux)'" in read(LINUX_SPEC) else "exe name not at this version"
    rd = read(README)
    probs = []
    if f"# MT2 Fishing Bot — v{v}" not in rd:
        probs.append("title")
    if f"**Download v{v}:**" not in rd or asset_url(v, win_asset(v)) not in rd:
        probs.append("download links")
    if not re.search(rf"^- \*\*{re.escape(v)}\*\* — ", rd, re.M):
        probs.append("version history")
    res["README.md"] = ", ".join(probs) + " not at this version" if probs else None
    res["src/version.py history"] = None if re.search(rf"^# {re.escape(v)} - ", read(VERSION_FILE), re.M) else "no history line"
    return res


def cmd_status(args):
    v = current_version()
    print(f"version (src/version.py): {v}")
    code, tags = out(["git", "tag", "--list", "Mt2-Fishbot*"])
    tag_exists = tag_name(v) in tags.split()
    code, rel = out(["gh", "release", "view", tag_name(v), "--repo", REPO, "--json", "tagName"])
    released = code == 0
    print(f"tag {tag_name(v)}: {'exists' if tag_exists else 'none'}; "
          f"GitHub release: {'exists' if released else 'none'}")
    print("  -> " + (f"{v} is released: bump to a new version" if (tag_exists or released)
                     else f"{v} is not released yet: release it as {v}"))
    code, last = out(["gh", "release", "list", "--repo", REPO, "--limit", "1", "--json", "tagName",
                      "--jq", ".[0].tagName"])
    print(f"latest GitHub release: {last if code == 0 else '?'}")
    print("files:")
    for name, prob in consistency(v).items():
        print(f"  {'ok ' if prob is None else 'OUT'} {name}{'' if prob is None else ': ' + prob}")
    print("tooling:")
    for label, ok in [
        (".venv python + PyInstaller", os.path.exists(os.path.join(ROOT, ".venv", "bin", "pyinstaller"))),
        ("wine", shutil.which("wine") is not None),
        ("Windows build env (~/.local/share/fishbot-winbuild)",
         os.path.exists(os.path.expanduser("~/.local/share/fishbot-winbuild/py/tools/python.exe"))),
        ("xvfb-run (smoke tests)", shutil.which("xvfb-run") is not None),
        ("gh logged in", out(["gh", "auth", "status"])[0] == 0),
    ]:
        print(f"  {'ok ' if ok else 'NO '} {label}")
    rd = release_dir(v)
    if os.path.isdir(rd):
        print(f"built assets in {os.path.relpath(rd, ROOT)}:")
        for f in sorted(os.listdir(rd)):
            p = os.path.join(rd, f)
            if os.path.isfile(p):
                print(f"  {os.path.getsize(p) / 1e6:8.1f} MB  {f}")
    code, branch = out(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    code, dirty = out(["git", "status", "--porcelain"])
    print(f"git: branch {branch}, {len(dirty.splitlines()) if dirty else 0} changed/untracked paths")


# ---------------------------------------------------------------- build

def smoke(cmd, env, seconds=25):
    """Starts the program on a private X display; ok if it is still running
    after `seconds` (the GUI came up and did not crash)."""
    env = dict(env)
    env.pop("WAYLAND_DISPLAY", None)       # never the real desktop
    env["QT_QPA_PLATFORM"] = "xcb"
    full = ["xvfb-run", "-a", "-s", "-screen 0 1920x1080x24", "timeout", "-s", "TERM", str(seconds)] + cmd
    print("$", " ".join(full), flush=True)
    t = time.time()
    r = subprocess.run(full, cwd=release_tmp, env=env, capture_output=True, text=True)
    if r.returncode == 124:
        print(f"smoke ok: still running after {seconds}s")
        return True
    print(f"smoke FAILED: exited with {r.returncode} after {time.time() - t:.1f}s")
    print((r.stdout + r.stderr)[-4000:])
    return False


release_tmp = None


def cmd_build(args):
    global release_tmp
    v = current_version()
    probs = {k: p for k, p in consistency(v).items() if p}
    if probs:
        sys.exit("version files are not at " + v + ": " + "; ".join(f"{k}: {p}" for k, p in probs.items())
                 + "\nrun: python tools/release.py bump " + v)
    rd = release_dir(v)
    raw = os.path.join(rd, "raw")
    os.makedirs(raw, exist_ok=True)
    release_tmp = os.path.join(rd, "smoke")
    os.makedirs(release_tmp, exist_ok=True)
    win_raw = os.path.join(raw, f"{APP} v{v}.exe")
    lin_raw = os.path.join(raw, f"{APP} v{v} (linux)")
    ok = True

    if not args.skip_windows:
        run([os.path.join(ROOT, "tools", "build_windows_exe.sh"), "--distpath", raw])
        if not os.path.exists(win_raw):
            sys.exit(f"Windows build produced no {win_raw}")
        shutil.copy2(win_raw, os.path.join(rd, win_asset(v)))
        archive = os.path.join(ROOT, "releases", "windows")
        if os.path.isdir(archive):
            shutil.copy2(win_raw, os.path.join(archive, os.path.basename(win_raw)))
        if not args.no_smoke and shutil.which("xvfb-run"):
            env = dict(os.environ,
                       WINEPREFIX=os.path.expanduser("~/.local/share/fishbot-winbuild/prefix"),
                       WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=")
            ok = smoke(["wine", win_raw], env, seconds=45) and ok
            subprocess.run(["wineserver", "-k"], env=env)

    if not args.skip_linux:
        run([os.path.join(ROOT, ".venv", "bin", "pyinstaller"), "--clean", "--noconfirm",
             "--distpath", raw, "--workpath", os.path.join("build", "linux"), LINUX_SPEC])
        if not os.path.exists(lin_raw):
            sys.exit(f"Linux build produced no {lin_raw}")
        tgz = os.path.join(rd, linux_asset(v))
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(lin_raw, arcname=os.path.basename(lin_raw))
        if not args.no_smoke and shutil.which("xvfb-run"):
            ok = smoke([lin_raw], os.environ) and ok

    sums = []
    for name in (win_asset(v), linux_asset(v)):
        p = os.path.join(rd, name)
        if os.path.exists(p):
            sums.append(f"{sha256(p)}  {name}")
    write(os.path.join(rd, "SHA256SUMS.txt"), "\n".join(sums) + "\n")
    print("\nassets in", os.path.relpath(rd, ROOT))
    for line in sums:
        h, name = line.split("  ")
        print(f"  {os.path.getsize(os.path.join(rd, name)) / 1e6:8.1f} MB  {name}  sha256 {h}")
    if not ok:
        sys.exit("a smoke test failed (see above)")


# ---------------------------------------------------------------- publish

def cmd_publish(args):
    v = current_version()
    tag = tag_name(v)
    rd = release_dir(v)
    assets = [os.path.join(rd, n) for n in (win_asset(v), linux_asset(v), "SHA256SUMS.txt")]
    missing = [a for a in assets if not os.path.exists(a)]
    if missing:
        sys.exit("missing assets (run build): " + ", ".join(os.path.relpath(m, ROOT) for m in missing))
    if not os.path.exists(args.notes_file):
        sys.exit(f"notes file not found: {args.notes_file}")
    code, head_vp = out(["git", "show", "HEAD:src/version.py"])
    if code != 0 or current_version(head_vp) != v:
        sys.exit(f"HEAD's src/version.py is not {v}: commit the release first")
    probs = {k: p for k, p in consistency(v).items() if p}
    if probs:
        sys.exit("version files disagree: " + "; ".join(f"{k}: {p}" for k, p in probs.items()))
    code, dirty = out(["git", "status", "--porcelain", "--", "src/version.py", "README.md", "packaging"])
    if dirty:
        sys.exit("uncommitted changes to release files:\n" + dirty)
    code, branch = out(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "main":
        sys.exit(f"on branch {branch}, releases are made from main")
    if out(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"])[0] == 0:
        sys.exit(f"tag {tag} already exists")
    if out(["gh", "release", "view", tag, "--repo", REPO])[0] == 0:
        sys.exit(f"GitHub release {tag} already exists")

    run(["git", "tag", "-a", tag, "-m", f"v{v}"])
    run(["git", "push", "origin", "main"])
    run(["git", "push", "origin", tag])
    cmd = ["gh", "release", "create", tag, *assets, "--repo", REPO, "--verify-tag",
           "--title", f"v{v}", "--notes-file", args.notes_file]
    if args.draft:
        cmd.append("--draft")
    else:
        cmd.append("--latest")
    run(cmd)
    print(f"\nreleased: https://github.com/{REPO}/releases/tag/{tag}")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    b = sub.add_parser("bump")
    b.add_argument("version")
    b.add_argument("--notes", help="one-line changelog for version.py and the README history")
    b.add_argument("--virustotal", help="VirusTotal URL to add to the README download line")
    b.set_defaults(fn=cmd_bump)
    bd = sub.add_parser("build")
    bd.add_argument("--skip-windows", action="store_true")
    bd.add_argument("--skip-linux", action="store_true")
    bd.add_argument("--no-smoke", action="store_true", help="skip the Xvfb start-up checks")
    bd.set_defaults(fn=cmd_build)
    pb = sub.add_parser("publish")
    pb.add_argument("--notes-file", required=True)
    pb.add_argument("--draft", action="store_true")
    pb.set_defaults(fn=cmd_publish)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
