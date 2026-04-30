# Interception driver setup

The bot uses [Interception](https://github.com/oblitum/Interception) — a signed kernel driver — to send mouse events that look identical to real hardware input. This bypasses the `LLMHF_INJECTED` flag that gives away `SendInput`-based clickers (PyAutoGUI, AutoHotkey, etc.).

If Interception is not installed, the bot silently falls back to PyAutoGUI. The header pill shows which one is active:

- `INPUT: INTERCEPTION` — kernel driver is loaded and being used.
- `INPUT: PYAUTOGUI` — fallback in use (driver not installed, or install incomplete).

---

## Automated setup (recommended)

### Easiest: Double-click the launcher

1. Open File Explorer and go to `<repo>\tools\`.
2. **Double-click `setup_interception.bat`**.
3. A UAC prompt will appear — click **Yes**.
4. The installer runs in PowerShell. When it finishes, the window stays open so you can read the output.
5. **Reboot Windows.**
6. Start the bot — the header should read `INPUT: INTERCEPTION`.

### Alternative: Copy-paste command

If you prefer the command line, open any PowerShell window and paste:

```powershell
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','C:\Users\boris\Documents\Mt2-Fishbot\tools\setup_interception.ps1'"
```

### Alternative: Run from Explorer

If you prefer the GUI:

1. Open **File Explorer** and navigate to `<repo>\tools\`.
2. Right-click **`setup_interception.ps1`** → **Run with PowerShell**.
3. If you see an execution-policy error, use the copy-paste command above instead.

### Options

For 32-bit Python, append `-X86` to the command above:

```powershell
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','C:\Users\boris\Documents\Mt2-Fishbot\tools\setup_interception.ps1','-X86'"
```

To download/extract only (no driver install — useful if you want to inspect the files first):

```powershell
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','C:\Users\boris\Documents\Mt2-Fishbot\tools\setup_interception.ps1','-SkipInstall'"
```

---

## Manual setup

If you'd rather do it by hand:

1. Download `Interception.zip` from [the v1.0.1 release page](https://github.com/oblitum/Interception/releases/tag/v1.0.1).
2. Extract anywhere — e.g. `C:\Tools\Interception`.
3. Open **PowerShell as Administrator**.
4. Run the installer: `& "C:\Tools\Interception\command line installer\install-interception.exe" /install`
5. **Reboot** Windows.
6. Copy the matching DLL to the repo root so ctypes finds it:
    - 64-bit Python → `C:\Tools\Interception\library\x64\interception.dll` → `<repo>\interception.dll`
    - 32-bit Python → `C:\Tools\Interception\library\x86\interception.dll` → `<repo>\interception.dll`
7. Start the bot.

You can place the DLL elsewhere and point at it explicitly via `bot_config.json`:

```json
"interception_dll_path": "C:\\Tools\\Interception\\library\\x64\\interception.dll"
```

---

## Configuration keys (`bot_config.json`)

| Key                          | Default     | Meaning |
|-----------------------------|-------------|---------|
| `input_backend`             | `"auto"`    | `"auto"` = try Interception, fall back to PyAutoGUI. `"interception"` = require Interception (still falls back if it fails, but logs a warning). `"pyautogui"` = force the SendInput path. |
| `interception_dll_path`     | `""`        | Optional explicit path to `interception.dll`. Empty → search standard locations. |
| `interception_mouse_device` | `null`      | Optional Interception device id (11–20). `null` → auto-pick the first responding mouse device. Set this only if you have multiple mice and the wrong one is being chosen. |

---

## Verifying it works

After reboot, start the bot. The header pill should read **`INPUT: INTERCEPTION`** and hovering it should say *“Interception kernel driver active — input is indistinguishable from a real mouse.”*

If it still says `PYAUTOGUI`:

- Hover the pill — the tooltip lists the reason Interception was rejected.
- Common causes:
    - **DLL not found** → script didn't copy it, or you have the wrong arch (32 vs 64 bit Python). Re-run with the right `-X86` flag, or copy by hand.
    - **`interception_create_context returned NULL`** → driver not installed or you didn't reboot. Open an elevated cmd, run `sc query interception` — `STATE` should be `RUNNING`. If it isn't, run `sc start interception` (or just reboot).
    - **No mouse device responded** → reboot. Interception needs to register your mice at boot.

---

## Uninstalling

```powershell
# From elevated PowerShell, after `cd` into the extracted Interception folder:
& ".\command line installer\install-interception.exe" /uninstall
# Reboot.
```

You can then delete `<repo>\interception.dll` and `<repo>\tools\interception\` if you want to reclaim the disk space.

---

## Why bother?

`SendInput` (used by PyAutoGUI, pynput, AutoHotkey, Razer/Logitech macros) sets a flag on every event that any user-mode program can read with `GetMessageExtraInfo` / `LowLevelMouseProc`. Anti-cheats use this to flag automated input. Interception sits below that layer — events are delivered to the OS as if from a real USB device, with no flag and no hook visibility.

The only thing more invisible than this is a physical USB HID device (e.g. Raspberry Pi Pico flashed as a mouse). Both are valid endpoints; Interception trades a one-time driver install for not needing hardware.
