# MT2 Fishing Bot — v2.0.4

Free fishing-minigame bot for Metin2. No subscriptions, no licenses.
**Built for educational and learning purposes only.**

[Video tutorial](https://youtu.be/M0THxnio894) · [Download v2.0.4](https://github.com/hetzpvp/Mt2-Fishbot/releases/latest) · [Discord server support](https://discord.gg/vZaHCRFqW7)

**Download v2.0.4:** [Windows .exe](https://github.com/hetzpvp/Mt2-Fishbot/releases/download/Mt2-Fishbot-v2.0.4/Fishing-Puzzle-Player-v2.0.4-windows.exe) · [Linux binary](https://github.com/hetzpvp/Mt2-Fishbot/releases/download/Mt2-Fishbot-v2.0.4/Fishing-Puzzle-Player-v2.0.4-linux.tar.gz) · [All releases](https://github.com/hetzpvp/Mt2-Fishbot/releases)

![GUI](docs/gui_demo.png)

<details>
<summary>More screenshots</summary>

![Positions](docs/gui_inventory.png)
![Fishing options](docs/gui_settings.png)

</details>

## Features

- Up to 8 Metin2 clients at once, new minigame and classic fishing
- Reads the screen only; it never touches game memory
- Auto fish handling per fish type (keep / open / drop, or use bait fish like Vai and Shrimp as bait), dead fish included, across up to 8 inventory pages
- Handles the Golden Tuna dialog with the choice you set
- Built-in jigsaw solver for the fishing puzzle: finds the puzzle window by itself in every client, wherever it is
- Up to 8 bait hotkeys (1–4, F1–F4), quick skip (horse or armor), adjustable timings, human-like clicking
- Adapts to re-skinned minigames, saves and loads profiles
- **F5** pauses and resumes every bot, with a short chime you hear while the game is in front
- Runs on Windows, and on Linux with the game under Wine ([docs/LINUX.md](docs/LINUX.md))
- Optional **Disguise in Task Manager** (Settings → Detection bypass): a new random, ordinary name at every start (see below)

## Quick start

1. Run the bot **as administrator** (Windows) and pick your Metin2 windows. The setup wizard (the highlighted **Fishing setup** / **Jigsaw setup** button at the top right) walks you through the rest, and the **Guide** button (bottom bar, and on each setup card) shows with pictures where to click for every coordinate.
2. In the **Positions** tab, set the position of inventory page 1 and of every further page your client has, in order, so the bot can switch pages when one fills up.
3. Open the inventory and leave it open: the bot reads it to know where catches land and when a page is full, also with automatic fish handling off. Optional: turn on automatic fish handling and choose what to do with each fish.
4. Put bait in the selected hotkey slots and press **START FISHING**.

Quick skip needs a horse (Horse mode), or a spare armor in the inventory with its position set as **Spare Armor** (Armor mode: the bot right-clicks it, which swaps it with the worn armor). Set the page tabs before Spare Armor: the bot then knows the armor's page and switches to it for the right-click while it fills other pages.

## Troubleshooting

| Problem | Fix |
|---|---|
| Bottom of the window cut off by the screen | **Settings → Interface size**: pick a smaller size (or Auto) and restart the bot, or lower the Windows display scale. Below 70 % the window gets shorter and its tabs scroll instead |
| Fish are not opened or dropped | Keep the inventory open, set the drop and confirm positions, then raise the item timings in **Timing Settings** (useful on high ping) |
| Jigsaw pieces not detected | Keep the whole puzzle window visible and uncovered. Still failing: send a diagnostic log (below) |
| Jigsaw pauses: "puzzle window not found in this game window" | Open the Fish Puzzle window in that client, away from the middle of the screen, with nothing over it, then press F5. If your server's puzzle looks different and it is still not found, press **Define Grid** (JIGSAW tab) and lay the ghost grid on the 24 cells |
| Jigsaw won't start or pauses: "the Confirm point lies on the puzzle grid" | The game opens its confirm dialog in the middle of the screen. Move the puzzle window away from the centre (top left works), then Set Confirm (JIGSAW tab) again — see **Guide → Jigsaw: window placement** |
| Pages stop switching | Re-set the page positions after changing resolution |
| Stops with "Game screen lost" | The inventory vanished while no fish bit (disconnect, death, login screen). Log back in, open the inventory, start again |

The game window can sit in the background but must not be minimized.

### Disguise in Task Manager

**Settings → Detection bypass → Disguise in Task Manager** gives the bot a new random name at every start, for example "Pixel Studio" or "Cedar Journal". Task Manager, System Monitor, `ps` and htop show that name, and so does the window title. Restart the bot after turning it on (the **Restart now** button next to the option does that).

At every start the bot copies its own file to your temp folder (`~/.cache` on Linux) under the new name, starts the copy and closes itself. The next start deletes the copies that are no longer running. Settings and diagnostic logs stay next to the original .exe. Starting takes a little longer, and the copy needs as much free disk space as the bot's file. It only works in the downloaded program, not when run from source.

This is one extra layer of privacy, not a way to become invisible. It only changes the name. The monkey icon in Task Manager, the file size, the memory use, the libraries it loads (Qt, Python) and the bot's window look the same as always to anyone who looks closely, for example with Process Explorer. It doesn't hide anything from the game either.

### Sending a diagnostic log

If something goes wrong and the table above does not help, send a diagnostic log:

1. **Settings** tab → tick **Diagnostic log**.
2. Press **START FISHING** (or **START JIGSAW**) and do what triggers the problem, then stop the bot (or let it stop by itself). A short run is best. Note roughly what time it went wrong and what you saw.
3. A file like `diagnostics/20261115_143022.zip` is written **next to the bot's .exe**. The Settings tab shows its path and **Open log folder** opens the folder with it selected.
4. Send that one `.zip` (the newest one) in the [Discord support server](https://discord.gg/vZaHCRFqW7).

It contains what the bot did, what it saw (screenshots of the game window only, never of the rest of the desktop) and the settings it used. If the bot's folder cannot be written to (for example under Program Files), the log goes to a per-user folder instead and the Settings tab shows where. Turn **Diagnostic log** off again afterwards; when it is off, nothing is recorded. Only the last 10 logs are kept.

**Record game windows** (below the Diagnostic log box) also puts a video of each game window into the same `.zip`. Turn it on only when support asks for it. The files are large: about 60 MB per minute per 800x600 window, at most 2 GB per run. The video shows everything in the game windows, including chat and character names, but never the rest of the desktop.

## Version history

- **2.0.4** — Jigsaw grid setup fixed on 1080p and larger screens
- **2.0.3** — Golden Tuna dialog, dead and bait fish handling, fish selection tabs, Task Manager disguise, faster catches, drop fixes, smaller Windows exe
- **2.0.2** — Lighter and faster with several clients, fishing and jigsaw loop fixes, diagnostic log, setup wizards and help pages
- **2.0.1** — Clickable Discord server support button
- **2.0.0** — Linux port (X11/Wine), OS abstraction layer, adaptive detection, profiles
- **1.2.0** — Jigsaw solver, new GUI
- **1.1.x** — Multi-page inventory, timing settings, debug overlays
- **1.0.x** — Classic fishing, fish dropping, fixes

## Development

Test it without the game using the built-in simulator: `python tools/minigame_sim` ([docs/SIMULATOR.md](docs/SIMULATOR.md)). Tests: `python tools/run_tests.py`.

**Planned:** skip the minigame based on chat messages.

## Donations

BTC: `3AGrrTf1v9QZsMPEoezYTRbf9JyW4nQtHu`

*For personal use only. Use at your own risk.*
