# MT2 Fishing Bot: notes for Claude

## Release flow

**Follow this flow every time the user asks for a new release** ("new release",
"generate a release", "release 2.1.0", "ship it", ...). Do every step in
order. Steps 1–6 are local and headless, so run them without asking. Step 7 is
the only stop before anything public happens.

The mechanical parts are in `tools/release.py` (`status`, `bump`, `build`,
`publish`); its docstring has the details. Always run it with `.venv/bin/python`.

### 1. Check the current state

```bash
.venv/bin/python tools/release.py status
```

This shows the current version, whether it's already tagged or released, which
version files disagree, whether the tooling is ready (Wine build env,
PyInstaller, xvfb-run, gh login) and the git state. Fix any missing tooling
before going on.

### 2. Pick the version

- If `status` says the current version is **not released yet**, release that
  version. It was bumped during development.
- Otherwise bump: the **patch** number by default (2.0.2 → 2.0.3). Use
  **minor** for new features or big rewrites, and **major** only when the user
  asks for it. A version the user names always wins.

### 3. Write the changelog

- Read what changed since the last release: `git log <last tag>..HEAD --oneline`,
  `git status` and `git diff --stat` (uncommitted work goes into the release
  too). Tags look like `Mt2-Fishbot-vX.Y.Z`.
- **One-line summary** for `version.py` and the README history. Write it for
  users, short, in the style of the existing entries (e.g. "Lighter and faster
  with several clients, fishing and jigsaw loop fixes, diagnostic log").
  If an entry for this version already exists, keep it unless it's now
  incomplete.
- **Release notes** for GitHub: write them to the scratchpad as
  `release-notes-X.Y.Z.md`, using this template:

  ```markdown
  ## What's new
  - <user-facing change>
  - ...

  ## Download
  - **Windows:** `Fishing-Puzzle-Player-vX.Y.Z-windows.exe`. Run it as administrator.
  - **Linux** (game under Wine): `Fishing-Puzzle-Player-vX.Y.Z-linux.tar.gz`.
    Unpack it with `tar xzf` and run the binary. Setup is in [docs/LINUX.md](https://github.com/hetzpvp/Mt2-Fishbot/blob/main/docs/LINUX.md).

  SHA-256 checksums are in `SHA256SUMS.txt`.

  Support: [Discord server](https://discord.gg/vZaHCRFqW7)
  ```

### 4. Bump the version and update the README

```bash
.venv/bin/python tools/release.py bump X.Y.Z --notes "<one-line summary>"
```

This updates `src/version.py` (VERSION and the history line), both PyInstaller
specs, and the README title, download links (GitHub release assets) and version
history. It's safe to run again. Add `--virustotal URL` if the user gives a
VirusTotal link.

Then read the README and fix anything the release made untrue (features,
troubleshooting, quick start). Only correct facts; don't rewrite it.

### 5. Run the tests

```bash
.venv/bin/python tools/run_tests.py
```

These are the unit tests. They never touch the desktop, so they're safe while
the user plays. **If anything fails, stop and report it** with the output.
Don't release over failing tests.

### 6. Build both binaries

```bash
.venv/bin/python tools/release.py build
```

Run it with `run_in_background` (it takes several minutes). It builds:

- the Windows exe with Wine (`tools/build_windows_exe.sh`). Headless, so don't
  ask first.
- the Linux binary (`packaging/build_linux.spec`, `.venv` PyInstaller).

It then smoke-tests both on a private Xvfb display. Each one must still be
running after a few seconds; this never touches the user's desktop. Each
asset that passed is recorded with its SHA-256 in `VERIFIED.json`; `publish`
refuses an asset that is missing from it (built with `--no-smoke`, smoke
failed, or rebuilt since). The assets go to `dist/release-X.Y.Z/`:
`Fishing-Puzzle-Player-vX.Y.Z-windows.exe`,
`Fishing-Puzzle-Player-vX.Y.Z-linux.tar.gz` and `SHA256SUMS.txt`. A copy of
the exe also goes to `releases/windows/` (the local archive, gitignored).
Warnings about broken tkinter or a missing numba `tbb12.dll` during the
Windows build are harmless.

If the build or a smoke test fails, stop and report it.

### 7. Confirm with the user (the only question)

Use AskUserQuestion and show:
- the version and the one-line summary
- the release notes (or their path)
- the asset sizes and the test result
- what goes into the release commit: a `git status --short` summary

Offer three options: **Publish**, **Publish as draft** or **Stop**. Nothing
leaves the machine before the user answers.

### 8. Commit

Stage the release: the version bump plus all uncommitted project work the
user confirmed in step 7. Never stage `bot_config.json`, `dist/`, `build/`,
`*.exe` or other local or secret files. Use the existing message style:

```
X.Y.Z: <one-line summary, lowercase start>
```

End the message with the attribution lines from the session's system reminder.

### 9. Publish

```bash
.venv/bin/python tools/release.py publish --notes-file <scratchpad>/release-notes-X.Y.Z.md   # add --draft if chosen
```

This checks that HEAD carries the version, the work tree is clean (no
changes to tracked files, no untracked files under `src/`, `packaging/` or
`assets/`), both assets passed their smoke test, the branch is `main`, and
that the tag and release don't exist yet. It then runs the unit tests on the
committed tree (about 3 minutes, so use `run_in_background`) and stops if
they fail. Then it creates the annotated tag `Mt2-Fishbot-vX.Y.Z`, pushes `main` and the tag,
and creates the GitHub release (title `vX.Y.Z`, marked latest) with the three
assets.

If it fails after the tag was pushed (for example the upload broke), don't
delete or move the tag. Finish by hand with
`gh release create Mt2-Fishbot-vX.Y.Z dist/release-X.Y.Z/{*.exe,*.tar.gz,SHA256SUMS.txt} --repo hetzpvp/Mt2-Fishbot --verify-tag --title vX.Y.Z --notes-file ...`,
or `gh release upload ... --clobber` for a missing asset.

### 10. Generate the Discord and elitepvpers posts

```bash
.venv/bin/python tools/release.py announce --notes-file <scratchpad>/release-notes-X.Y.Z.md
```

This fills the templates in `tools/announce/` (`discord.md`,
`elitepvpers.txt`, BBCode) and writes `dist/release-X.Y.Z/announce-discord.md`
and `announce-elitepvpers.txt`. The "What's new" bullets come from the release
notes, and the downloads point at the GitHub release assets. Add
`--mediafire URL` and `--virustotal URL` if the user has them, and run it
again when they add one later. Nothing gets posted: show both texts to the
user in the report so they can copy them. If the user sends a newer post as
the template, update the file in `tools/announce/` (keep the `{{...}}`
placeholders) and commit it.

### 11. Report

Give the release URL, the asset download URLs, the SHA-256 sums and the two
announcement texts. Mention
that the README now links to the GitHub release assets. A MediaFire mirror or
VirusTotal scan is optional and manual: if the user adds one, run
`bump X.Y.Z --virustotal URL` and commit the README change.

### Never

- Force-push, rewrite published history, or delete or re-tag a published
  release without the user asking.
- Publish with failing tests or a failed smoke test.
- Commit release binaries. They're 150–230 MB, over GitHub's file limit, and
  belong in the release assets.
