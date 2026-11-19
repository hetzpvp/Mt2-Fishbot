# UI/UX audit — 2026-11-18

The window is usable. A first run points at a setup wizard, start and stop are labeled, a failed start leaves the window unlocked, and the fixes already covered by `tests/test_ui_ux_audit_fixes.py` still hold (section 7). The problems below are places a player is told the wrong thing, loses the Next or Save button on a short screen, or sees a status that does not match the bot.

## 1. Summary

| Rank | Screen | Issue | Impact | Effort |
|---|---|---|---|---|
| 1 | Fishing setup, Catch | The sentence says every drop needs Drop / Sell. The picture on the same step, the guide, and Start say to leave Drop / Sell empty when the client only asks yes/no. | friction | S |
| 2 | Wizard, fish dialog, timing, guide | At the 70% scale used for a very short screen, Next and Save sit below the screen. The main window was shortened for that screen. These dialogs were not. | friction | M |
| 3 | Dashboard, while paused | Reset Bait stays disabled. The guide and the bait notice both say to press it after restocking, which is when the player has paused. | friction | S |
| 4 | Dashboard rows | The pill says READY as soon as a window is chosen, including while the status says "Not ready yet", and again after a mode switch when that window is closed. | friction | S |
| 5 | Fish & items | Three tiles are named from the wrong file: Large sandfish, Refugee coin, Symbol wing. | friction | S |
| 6 | Fishing tab, Quick Skip | Horse "presses the mount hotkey" and never says which key. The key is G, and there is no control for it. | friction | S |
| 7 | Jigsaw tab | A notice that says "Press Reset Bait" stays up in Jigsaw mode, where that button is hidden, and it pushes the other crate choices under the footer. | friction | S |

## 2. Method and environment

- Python 3.14.10, PySide6 6.12.0, Qt 6.12.0, Linux (`Linux-7.2.15-200.fc44.x86_64`).
- Screenshots and the flow walk used Qt platform `offscreen`. That screen is 800×800, device pixel ratio 1, 96 DPI. The app was built at 100% because this pass did not go through `main()` (that is what applies `QT_SCALE_FACTOR`).
- Dialog fit was measured again on a private Xvfb, not the user's display: 800×600 and 768×432 at 100%, then the same screens with `QT_SCALE_FACTOR=0.85` and `0.7`, which is what `choose_ui_scale("auto", …)` returns for those sizes. No mouse moves and no keys were sent to the session.
- `pynput` was stubbed and `platforms.native` was set to `None` before the window existed, so no global hook and no uinput device. The solver build was a no-op. Game windows and fishing workers were fakes. `FISHBOT_MUTE=1`. The "Input method: standard (PyAutoGUI)" line in the settings shot is that stub. A normal Linux launch probes uinput instead.
- Sizes rendered offscreen: the real window 920×664, the same window forced to 800×600 and 1280×900, and a window built at height 604 (what `window_height_for(432, 0.7)` returns). Dialogs at the sizes they choose.
- Baseline: `.venv/bin/python tools/run_tests.py` — **500 passed** in 213.69s. OS-layer tests were not run.
- Fixes already locked in by `tests/test_ui_ux_audit_fixes.py` were exercised again. They are in section 7.

Claims are marked **observed** (screenshot, offscreen run, or the Xvfb geometry), **read** (source only), or **inferred** (arithmetic from those two, where a full scaled launch of `main()` was not repeated).

## 3. Screen inventory

| Screen | File and class | How a player reaches it |
|---|---|---|
| Main window | `src/qt_gui.py` `FishbotWindow` | Start the program. Fixed 920×664, frameless, shorter only when the screen is too short at 70%. |
| Header | `Header` | Always visible. Drag to move. Minimize and Close. Status pill. Discord. |
| Mode bar | `ModeBar` | Under the header. Fishing or Jigsaw Puzzle, plus that mode's setup button. |
| Dashboard | `DashboardTab` | Tab Dashboard. Start / Pause, Stop, F5 hint, ready chips, eight window rows. |
| Positions | `InventoryTab` | Tab Positions. Page tabs, Drop / Sell, Confirm, Spare Armor, fish handling. |
| Fishing options | `SettingsTab.fishing_page` | Tab Fishing, only in Fishing mode. |
| Jigsaw options | `JigsawTab` | Tab Jigsaw, only in Jigsaw mode. |
| Settings | `SettingsTab` | Tab Settings. Capture, profiles, diagnostics, interface size, accent. |
| Notice | `NoticeBar` | Above the tab body when there is something to say. |
| Setup wizard | `SetupWizard` | Fishing setup or Jigsaw setup, or the first-run notice. Not modal. Opens at 744×700 (it asks for 720 and grows to fit the step names). |
| Fish & items | `FishModal` | Positions → Configure Fish and Items, or the wizard's Catch step. 720×660, minimum 700×600. |
| Timing | `TimingModal` | Fishing tab → Timing Settings. 450×640. |
| Guide | `src/help_visuals.py` `HelpDialog` | Guide in the footer, on cards, and in the wizard. 880×640 for the full guide. |
| Diagnostic log | `DiagnosticLogDialog` | Settings → Show log. 980×560. |
| Report a problem | `FishbotWindow.open_report_dialog` | Footer. A message box. |
| Puzzle grid overlay | `JigsawGridOverlay` | Jigsaw tab or wizard → Define Grid. A separate always-on-top window the size of the game window. |
| Debug windows | `src/debug_ui.py` | Only if `DEBUG_MODE_EN` is true. It is false in `src/utils.py`. Players do not see these. |
| Standalone solver | `src/jigsaw_solver/app.py` `JigsawApp` | A Tk window. The Qt app does not open it. |

## 4. Flow walks

### First run

1. No config file. **Observed:** a notice, "New here? The setup wizard (top right, highlighted)…", with Open setup. The Fishing setup button is the amber one on the right. Status is Idle. The big line is "Not ready yet" and names Window and Inventory pages. Those two chips are red. Bait is green, because keys 1–4 are the default.
2. The notice uses a row of vertical space. **Observed** in `01_first_run.png`: the list shows W01–W07 and a scrollbar. W08's top is at y=279 in a 272px viewport. After the notice is dismissed, W08's bottom is at y=308 in a 324px viewport, so all eight rows fit.
3. Open setup. Fishing steps are Welcome, Windows, Inventory, Bait, Catch, Quick skip, Ready. Finish returns to the Dashboard. **Observed:** each of those steps fits its own scroll area at 744×700 (vertical maximum 0).
4. Start stays enabled while the chips are red. A click with no window selected shows "Please select at least one game window…". A click with a window and no Page 1 opens the inventory-tabs guide. **Observed.** The status line names what is missing, and a click on a red chip jumps to the right tab (**read**, `_on_fix_requested`).

### Start a fishing session, pause, stop

1. A client selected and Page 1 unset. **Observed:** status "Not ready yet", missing Inventory pages. The row pill already says READY (finding 5.4). Window and Bait chips are green.
2. Page 1 set. **Observed:** "Ready to start / Fishing in 1 window". All three chips green.
3. Start, with a fake worker. **Observed:** header `● FISHING`, button PAUSE, a red STOP, subtitle "in 1 window — F5 pauses", Jigsaw disabled, hint "Fishing is running — stop it to switch to Jigsaw Puzzle." The hint wraps to two lines and fits the 48px bar (needed height 30, had 36). The Running number reads `1/1`.
4. PAUSE. **Observed:** header `● PAUSED`, button RESUME, the row pill PAUSED, the fake worker's `paused` flag true. Refresh and Reset Bait stay disabled (finding 5.3). F5 does the same toggle. **Observed** via `_f5_toggle`.
5. STOP. **Observed:** header `● IDLE`, button START FISHING, the row says "Stopped by user".

### Change one setting

Unticking bait key 4 while one window's counted bait was 50 left that count at 50. The other selected window, which was full, went from 800 to 600. **Observed.** A notice said "Bait left was not refilled. Press Reset Bait after you restock in the game." No question dialog. That is the behavior the earlier fix asked for.

Interface size shows "now 100% (picked for this screen)" on this offscreen screen. **Observed.** That sentence is `main()`'s note, and this pass did not run `main()`, so it is not evidence about a real screen. Changing the choice to a value other than the running scale says to restart (**read**, `_update_scale_note`, and covered by `tests/test_gui_smoke.py`).

### Switch to Jigsaw

1. While fishing is running or paused, the Jigsaw button is disabled and the hint says to stop first. **Observed** in `09_running.png` and `10_paused.png`.
2. After a start that found the client closed, the row said NOT OPEN and the Window chip was red. Switching to Jigsaw rewrote the pill to READY and cleared the message. The chip stayed red and the status stayed "Not ready yet". **Observed** (finding 5.4).
3. On the Jigsaw tab with no notice, all three crate choices sit inside the first screen. **Observed** on Xvfb: the third radio's bottom is y=449 in a 468px viewport. With the bait notice still up, the viewport is 416px and the second and third choices are below it (finding 5.7).
4. Positions in this mode says pages are optional and are used to refill crate slots. **Observed.** Confirm is required. Drop / Sell and Spare Armor stay visible, locked, with a FISHING ONLY badge.

### Recover from an error

Client selected, then removed from the window list, then Start. **Observed:** a warning that names W1; the row pill is `● NOT OPEN`; the row message is "Window not open — start the game client or clear this slot"; the big status is "Not ready yet"; the Window chip is red.

A duplicate pick is refused. **Observed:** the second slot stays on its previous window and a notice says the name is already used by W1.

A position capture with two clients selected shows "Now click tab 1 of the inventory in the game window 'Metin2 (1)'. Esc or a right click cancels. The other selected clients stay minimized until you click or cancel." **Observed.**

Closing the window while a bot was set asked "Stop it and quit?" The default button is No (the question's default value is `QMessageBox.No`). Answering No left the bot in place. **Observed.**

## 5. Findings

### 5.1 The Catch step says every drop needs Drop / Sell

- Screen: Fishing setup, Catch. `src/qt_gui.py` `SetupWizard._build_fish` (line 3949). The picture under it is `draw_drop_confirm` in `src/help_visuals.py` (line 380). Start checks Confirm only (`start_all_bots`, line 6721).
- What the player sees: "Dropping needs Drop / Sell (the drop button, or the spot to drag items to) and Confirm." Under that, the picture says "Only a yes/no dialog: leave Drop / Sell empty and set only 2 Confirm." The guide step says the same thing (`help_visuals.py` line 650).
- What they would expect: one instruction. Clients that only show "Are you sure?" should leave Drop / Sell empty. Drop / Sell is the dialog button, when that dialog exists.
- Evidence: **observed** in `16_wizard_step_4_Catch.png` and in the step text collected from the open wizard. **Read:** the parenthetical calls Drop / Sell "the spot to drag items to". That spot is `drop_ground_pos` (line 5419), which has no control and defaults to the window center. The Drop / Sell pill stores `drop_button_pos`. Start does not require it.
- Impact: friction. A player who follows the sentence sets Drop / Sell on every client. On a client with no drop dialog, that click lands wherever they aimed, and the bot later clicks there as if it were the dialog button. The picture on the same page tells them to do the opposite.
- Fix: use the picture's rule in the sentence. "If the game first asks drop or sell, set Drop / Sell on that button. Always set Confirm on the Yes button. The bot drops on the ground by itself." Leave `drop_ground_pos` out of this sentence until it has a control.
- Test: `tests/test_wizard_tutorials.py` can open the Catch step and assert the intro contains "leave Drop / Sell empty" and does not contain "the spot to drag". `tests/test_help_visuals.py` already draws this picture.
- Confidence: observed.

### 5.2 Next and Save fall off a very short screen

- Screen: Fishing setup, Fish & items, Timing Settings, and the guide. Sizes are set in `SetupWizard` (line 3509, and it lays out at 744×700), `FishModal` (line 3146, 720×660), `TimingModal` (line 3377, 450×640), `HelpDialog` (line 798, 880×640). The main window uses `window_height_for` (line 189). The dialogs do not.
- What the player sees on a short screen: the main window fits. The wizard opens with Next below the bottom edge, and the fish and timing dialogs open with Save below it. The guide's Close does the same.
- What they would expect: the same rule as the main window. The buttons that finish the step stay on screen, and the step body scrolls.
- Evidence: **observed** on Xvfb 768×432 with `QT_SCALE_FACTOR=0.7`. Qt reported a logical screen of 1097×617. That scale is what `choose_ui_scale("auto", 768, 432)` returns, and 768×432 is about a 1080p display at 250% OS scale. Wizard Next, fish Save, timing Save, and guide Close were all below y=617. The log dialog (980×560) fit that screen. On 800×600 at 0.85, which is the auto scale for that size, every dialog except the log fit; the log is 980 wide against a logical width of 941, so its text runs past the right edge.
- The 100% Xvfb run is harsher than a real launch, because `main()` applies the scale first. It is recorded in `dialog_fit_800.json` and is not the claim above. The main window's own footer was also below y=617 in the 0.7 run, because this script builds the window at height 664. A real launch sets `ACTIVE_WINDOW_H` to 604 (**read**, `window_height_for`), and 604 fits in 617. **Inferred** for that one number: the main window is the piece that already fits.
- Impact: friction. On that short screen the player can still set up from the tabs, because the main window fits and its pages scroll. They cannot press Next, Finish, or Save in these dialogs unless they drag the window upward until the title bar is off the screen. Fish & items has a minimum height of 600, so it can be shrunk to fit 617 only if the bottom edge can be grabbed, and that edge is the part that is off the screen.
- Fix: after `show`, cap each dialog's height to the screen and leave the footer (Next, Save, Close) outside the scroll area. The wizard body, the fish list, the timing list, and the guide page already scroll. Cap the log's width the same way. `FishModal`'s minimum height of 600 has to drop below the screen height or the cap cannot win.
- Test: with `QT_SCALE_FACTOR=0.7` on a 768×432 screen, map Next and Save to global coordinates and assert their bottoms are ≤ `availableGeometry().bottom()`. A headless stand-in is to assert `700 * MIN_UI_SCALE` is not the rule and that the dialog's maximum height tracks the screen. `tests/test_ui_ux_audit_fixes.py` `test_short_screen_gets_a_shorter_window` covers the main window and should stay.
- Confidence: observed for the dialog coordinates at 0.7 and at 0.85. Inferred only for the real main-window height, which this Xvfb process did not build.

### 5.3 Reset Bait does nothing useful while the bot is paused

- Screen: Dashboard. `DashboardTab.setRunning` (line 2059) disables Reset Bait for the whole run. `setPaused` (line 2071) does not turn it back on. `_on_reset_bait_clicked` (line 6592) returns immediately while `self.bots` is set. The guide says to press Reset Bait after restocking (`help_visuals.py` line 666). The bait notice says the same (`_recalculate_window_bait`, line 6638).
- What the player sees: F5 pauses, the header says PAUSED, and the row says PAUSED. Refresh and Reset Bait are grey. The tooltip adds "Available when the bots are stopped."
- What they would expect: pause is how they get the mouse back to restock, and Reset Bait is the button the guide names for that moment.
- Evidence: **observed.** During the fake run, after PAUSE, `reset_btn.isEnabled()` was false and the worker's `paused` flag was true. **Read:** the running bot keeps its own `bait_counter` (`fishing_bot.py` line 299) and counts it down itself. Enabling the button without writing that counter would update the dashboard and leave the bot on the old count.
- Impact: friction. Restocking means Stop, Reset Bait, Start. Stop is the control that ends the session. The next Start also zeroes that window's games counter (`start_all_bots`, line 6808). If they restock during the pause and then resume, the bot keeps the old count and stops early, with bait still on the hotbar.
- Fix: allow Reset Bait while paused. Keep the question. On Yes, set the dashboard count and each paused bot's `bait_counter` to the full capacity. Leave it disabled while the bot is actively clicking. The tooltip can say "Pause or stop the bots first" while it is running.
- Test: `tests/test_gui_review.py` can start a fake bot, pause it, accept the question, and assert the fake's `bait_counter` equals keys × quantity. Today the same test should see the button disabled, which is the bug.
- Confidence: observed for the disabled button. Read for the separate counter.

### 5.4 The row pill says READY when the bot is not ready to start

- Screen: Dashboard, the window rows. A chosen window is set to READY in `_on_window_changed` (line 6269). Every mode switch sets it again in `set_mode` (line 5535) and clears the row message.
- What the player sees, first case: a window is selected, Page 1 is not set, the big line says "Not ready yet", the Inventory pages chip is red, and the row says READY. **Observed** in `07_window_selected.png`.
- What the player sees, second case: the client has closed, Start has marked the row NOT OPEN with the message "Window not open — start the game client or clear this slot", and the Window chip is red. Switching to Jigsaw Puzzle rewrites the pill to READY and clears the message. The chip stays red. **Observed.**
- What they would expect: READY to mean the same thing as the big line, "Ready to start". A closed client should keep NOT OPEN until Refresh or a new choice, which is what the chip already does.
- Evidence: both cases **observed**. **Read:** `set_mode` comments that "last run's messages belong to the other activity" and then sets READY for every selected row, including a row whose window is in `_gone_windows`.
- Impact: friction. The first case teaches the player that the slot is ready before the inventory is set, so they press the amber Start and get the guide instead. The second case hides the only line that said the client had closed. Start still refuses (**observed** for the closed client before the switch), so this does not start a bot in a closed window.
- Fix: label a chosen idle window SELECTED. Leave NOT OPEN and its message in place across a mode switch. Let Refresh and a new choice clear them, which they already do.
- Test: extend `tests/test_ui_ux_audit_fixes.py` `test_closed_client_is_not_ready` with `set_mode(MODE_JIGSAW)` and assert the pill text is still NOT OPEN. A second assertion: with Page 1 unset, the pill text is not READY.
- Confidence: observed.

### 5.5 Three tiles are named from the wrong file

- Screen: Fish & items. `FISH_DISPLAY` and `ITEM_DISPLAY` in `src/qt_gui.py` (lines 3089, 3118, 3120).
- What the player sees: "Large sandfish", "Refugee coin", and "Symbol wing". **Observed** in `19_fish_modal.png` and `20_fish_items.png`. The files behind them are `Large_zander_living.jpg`, `Refugee_cape_item.jpg`, and `Symbol_wise_emperors_item.jpg` (**read**).
- What they would expect: the name of the picture. A sandfish, a coin, and a wing are different items from a zander, a cape, and the wise emperor's symbol. The picture is what the bot matches, so the bot still handles the right file. The player configures the wrong tile, or skips it, when they search by the name in the game.
- The rest of the list mixes English and German (Aal, Barsch, Brookforel, Riverforel next to Carp and Shrimp). **Observed.** That mix is a naming choice, not a file mismatch. It is open question 1.
- Impact: friction. Keep / Drop / Open is stored per file name, so a wrong label sends the action to the wrong catch.
- Fix: rename those three to match the files once the intended English names are confirmed. Zander already has its own tile, so "Large sandfish" should not stay if the file is the large zander.
- Test: a one-line test can assert the display name for those three assets. `tests/test_ui_ux_audit_fixes.py` `test_saved_values_are_never_empty` already checks that an item cannot be saved as open.
- Confidence: observed for the labels, read for the file names.

### 5.6 Horse quick skip never says which key it presses

- Screen: Fishing tab, Quick Skip. The description is the label at line 2636. The guide says "Ctrl + the mount key (G by default)" (`help_visuals.py` line 671). The wizard says "Ctrl + mount key" (`_build_quick_skip`, line 3979). `mount_key` defaults to `"g"` and the comment above it says there is no GUI field yet (line 5418). `cast_key` defaults to space the same way (line 5417).
- What the player sees: "Horse presses the mount hotkey." **Observed** in `04_fishing.png`. Nothing on the Fishing tab shows G, and nothing edits it.
- What they would expect: the key they bound in the client. A client whose mount is not G gets Ctrl+G anyway (**read**, `fishing_bot.py` line 2493).
- Impact: friction. Official clients that use G are fine and never told that G is the assumption. Everyone else cannot fix it without editing `bot_config.json`. The same is true of the cast key, which the UI never names.
- Fix: two fields on the Fishing tab, "Cast key" and "Mount key", filled from `cast_key` and `mount_key`, saved like the bait keys. Put the current mount key in the Quick Skip sentence so it stays true when the field changes.
- Test: `tests/test_gui_smoke.py` can set the field, reload the window, and assert `config["mount_key"]`. The bot tests that call `press_ctrl_key` should keep using the config value.
- Confidence: observed for the sentence. Read for the missing field and the key the bot presses.

### 5.7 The bait notice follows the player into Jigsaw mode

- Screen: the notice bar, which sits above every tab. The Jigsaw tab hides Reset Bait (`set_mode`, line 5525). The crate list is `JigsawTab` (line 4510).
- What the player sees: after a bait-key change, the notice "Press Reset Bait after you restock in the game" is still there on the Jigsaw tab. Reset Bait is not on that dashboard. The notice also shortens the tab. **Observed** in `12_jigsaw_tab.png`: "Special crates first" is cut by the footer, and the third choice is below it. The second radio's bottom was y=425 in a 416px viewport.
- What they would expect: a notice about bait to stay on the Fishing dashboard, where the button is. The three crate choices to be on the first screen, which they are once the notice is gone.
- Evidence: **observed** both ways on Xvfb. With the welcome notice up, the second and third radios are outside the viewport. After `notice.clear("welcome")`, all three are inside (bottoms 378, 425, 449 in a 468px viewport). **Read:** `set_mode` does not clear the bait notice, and Reset Bait's widget is hidden for the whole time Jigsaw is selected.
- Impact: friction. The player cannot do what the notice says without switching back. The third crate strategy is a real option (`special_when_better` is saved). With a notice up, only "Normal crates only" is fully on screen.
- Fix: drop the bait notice when leaving Fishing mode, or name the mode in the sentence ("In Fishing, press Reset Bait…"). Keep showing only the selected crate's explanation, which already fits when the notice is absent.
- Test: `tests/test_ui_ux_audit_fixes.py` `test_all_crate_strategies_fit_the_first_screen` clears the welcome notice first, so it still passes. Add the notice back and assert the third radio is inside the viewport, or assert the bait notice is not visible in Jigsaw mode.
- Confidence: observed.

### 5.8 Smaller mismatches

- **Sound alert.** The Fishing checkbox says "Sound alert on no bait" (line 2558). The wizard checkbox bound to the same setting says "Sound alert when all windows are out of bait" (line 3935). The sound plays only when every window that started has stopped because its bait is gone (`_maybe_play_no_bait_alert`, line 6946). **Read.** One window running out is silent if another is still fishing. Use the wizard's sentence on the Fishing tab.
- **Copy.** The footer copy button copies the address and changes nothing else. **Observed:** after `_copy_btc`, the only notice still up was the bait one. A "Copied" on the button, for a second, is enough.
- **Two names for Confirm.** The Jigsaw button says "Set Confirm Coords" (line 4481). Positions, the wizard, and the ready chips say "Confirm". **Observed** the button. One name, "Confirm", matches the pill.
- **No focus ring on buttons.** The stylesheet gives `QLineEdit` and `QComboBox` an accent border on focus (line 539) and has no `QPushButton:focus` rule. **Read.** Offscreen grabs do not show a focus ring, so this stays unproven visually. Fields are fine.
- **Sage King Glove** wraps to two lines inside the tile and still fits (advance 117, width 106, needed height 36, had 36). **Observed** as a wrap, not a clip. No change unless the tile gets wider.

## 6. Accessibility checklist

| Item | Result | Evidence |
|---|---|---|
| Keyboard: start, stop, window combos, settings fields | Pass | **Read** and **observed** as focus policy. Start and Stop use `StrongFocus`. Line edits and combos take a border on focus (line 539). |
| Keyboard: position pills, accent swatches, wizard steps | Fail | **Observed.** Focus policy `NoFocus` (0) on the Page 1 pill, all six swatches, and every wizard step chip. The wizard has Back / Next. A position still needs a click in the game. |
| Visible focus on buttons | Not verified | No `QPushButton:focus` rule (**read**). The offscreen grab does not show a ring. |
| Accessible names on icon buttons | Pass on the main window | **Observed.** 16 of 17 icon buttons had a name. `name_icon_buttons` (line 1001) copies the tooltip. The one unnamed button had no tooltip, so it was skipped. |
| Accessible names on everything else | Mixed | Text buttons expose their caption through Qt. Custom pills do not set `accessibleName`. |
| Labels for inputs | Pass for the main fields | Bait quantity, classic delay, interface size, and timing rows have visible text. Window combos are labeled W01–W08. |
| Color-only status | Pass | IDLE, FISHING, PAUSED, EMPTY, READY, RUNNING, NOT OPEN are words plus a color. Ready chips use ✓ and ✗. **Observed.** |
| Color-only controls | Pass for checkboxes | Checked boxes use a tick image. **Observed** on the Fishing tab. Radios use a filled ring (**read**, line 603). |
| Contrast of body, dim, and hint text | Pass | `#e8e8ec` on `#131316` is 15.18:1. Dim `#9a9aa2` is 6.64:1. Mute `#85858e` is 5.07:1 on the page, 4.74:1 on cards (`#1a1a1e`), 5.38:1 on the mode bar (`#0b0b0c`). **Measured** from the tokens. |
| Contrast of disabled controls | Exempt | Disabled text `#6a6a73` is 3.46:1. The disabled mode button `#4a4a52` on `#131316` is 2.11:1. The orange hint next to it says why. |
| Scrollbar | Pass | Handle `#85858e` on `#131316` is 5.07:1. |
| Target size | Mixed | Start and Stop are 42px. Header Close is 40×44. Discord is 24px tall. Ready chips are 21px tall. Copy is 26×26. Accent swatches are 18×18. The position clear button is 20×20. The checkbox indicator is 14×14 in the stylesheet (line 572). |
| Screen reader | Not run | Follows from the names and the tab order. No reader was used. |

## 7. Rejected

Re-checked in this pass. Still holding, so they are not findings:

- Counted bait is kept when a key is unticked. **Observed:** a window at 50 stayed at 50. The window that was full followed the new capacity (800 to 600). The notice names Reset Bait.
- The footer address matches the README and the copy button copies that string. **Observed.**
- Timing rows fit the dialog width. **Observed:** horizontal maximum 0, and the millisecond labels (12ms, 400ms, 6000ms) are on screen in `21_timing.png`. Drop Action and Quick Skip are below the fold (the mount-key row is at y=648 in a 529px viewport). Vertical scrolling is the way those groups are reached. The intro is "These are the waits between clicks and key presses."
- The Positions card and the guide say items are keep or drop, and fish are keep, drop, or open. **Observed** in the positions text. Item tiles have K and D only. **Observed** in `20_fish_items.png`.
- The Jigsaw welcome no longer says the move table "is being prepared". **Read** at line 3819: "You can keep setting up while it is built." The status line under it is the part that changes.
- Refresh and Reset Bait look disabled while a bot runs. **Observed** in `09_running.png`. The stylesheet contains `BtnGhost:disabled`.
- With no notice, all three crate choices fit the Jigsaw tab at 920×664. **Observed** on Xvfb. The notice case is finding 5.7.
- A 768×432 screen keeps the 0.7 scale and shortens the main window (`window_height_for` returns 604). **Read.** The forced 800×600 grab is a squeeze the app does not ship. Auto scale at 800×600 is 0.85, and the window is 782×564.
- A second click on K, D, or O keeps the choice. Covered by `tests/test_ui_ux_audit_fixes.py` and not re-broken.
- Quick Skip off leaves Horse and Armor unchecked. **Observed** in `04_fishing.png`.
- Close while a bot runs asks, defaults to No, and No leaves the bot. **Observed.**
- The capture banner says the other clients stay minimized when more than one is selected. **Observed.**
- Running counts selected windows (`1/1`), not the eight slots. **Observed.**
- Forget learned colors asks, and No keeps the profile. **Observed.** The question's default value is `QMessageBox.No`.
- Eight rows fit at 920×664 after the welcome notice is dismissed. **Observed.**
- The mode hint while fishing fits the bar. **Observed.**
- Profile load says "Loading one replaces the current settings." **Observed** in `06_settings_bottom.png`.
- Jigsaw Positions says Page 1 is optional. **Observed.**
- Debug windows and `jigsaw_solver/app.py` are not on the player path.
- The grid overlay's instruction box is 490px wide (`JigsawGridOverlay.paintEvent`, line 4379). On a 480px-wide window the box border is clipped and the three lines of text still fit. **Observed** in `25_overlay_480.png`. Game windows are larger than that. Not worth a change.
- Reset Bait's question defaults to Yes. **Observed** (the default value is `QMessageBox.Yes`). The player opened that dialog by pressing Reset Bait. Forget learned colors defaults to No, which is the one that throws away a learned profile.

## 8. Open questions

1. Fish names: should the tiles use the English client names? The three file mismatches in finding 5.5 should change either way. The German names (Aal, Barsch, Brookforel, Riverforel) are a separate choice.
2. Mount key and cast key: should they be fields on the Fishing tab (finding 5.6), or is `bot_config.json` the place to change them?
3. Restocking: should Reset Bait work while paused and update the running bot's count (finding 5.3), or is Stop → Reset Bait → Start the path you want?
4. The no-bait alert plays a Rick Roll melody (`play_rickroll_beep` in `src/utils.py`). Keep it?
5. Human-like mouse timing is off on a fresh config. **Observed** on the Fishing tab. Should a new install turn it on?
6. For the short screen in finding 5.2: shrink the wizard and the other dialogs so Next and Save stay on screen, or leave them and rely on the tabs?

## 9. Appendix

Scratchpad (not in the repo):

- `/tmp/fishbot-ui-ux-audit-2026-11-18/audit_ui.py` — builds the window offscreen, walks the flows, writes screenshots and `audit_data.json`
- `/tmp/fishbot-ui-ux-audit-2026-11-18/audit_data.json` — measurements (contrast, clips, flows, scale table)
- `/tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit.py` — places the dialogs on Xvfb and records what falls off the screen
- `/tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit_800.json` — 800×600 at 100%
- `/tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit_768.json` — 768×432 at 100%
- `/tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit_800_scale085.json` — 800×600 at 0.85
- `/tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit_768_scale07.json` — 768×432 at 0.7
- `/tmp/fishbot-ui-ux-audit-2026-11-18/shots/` — 34 PNGs. The ones cited above are `01_first_run.png`, `04_fishing.png`, `07_window_selected.png`, `09_running.png`, `10_paused.png`, `12_jigsaw_tab.png`, `16_wizard_step_4_Catch.png`, `19_fish_modal.png`, `20_fish_items.png`, `21_timing.png`, `25_overlay_480.png`

Commands:

```bash
.venv/bin/python tools/run_tests.py

QT_QPA_PLATFORM=offscreen FISHBOT_MUTE=1 \
FISHBOT_CONFIG_DIR=/tmp/fishbot-ui-ux-audit-2026-11-18/config \
.venv/bin/python /tmp/fishbot-ui-ux-audit-2026-11-18/audit_ui.py

xvfb-run -a -s "-screen 0 768x432x24" \
env QT_QPA_PLATFORM=xcb QT_SCALE_FACTOR=0.7 FISHBOT_MUTE=1 \
.venv/bin/python /tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit.py

xvfb-run -a -s "-screen 0 800x600x24" \
env QT_QPA_PLATFORM=xcb QT_SCALE_FACTOR=0.85 FISHBOT_MUTE=1 \
.venv/bin/python /tmp/fishbot-ui-ux-audit-2026-11-18/dialog_fit.py
```

The 100% Xvfb runs used the same script without `QT_SCALE_FACTOR`. Scale figures in the table below are the return values of `choose_ui_scale` and `window_height_for`. The 0.7 and 0.85 dialog positions were measured with that factor set. The main window's shortened height was not rebuilt inside that scaled process.

| Logical screen | Auto scale | Window (width × height, after scale) |
|---|---|---|
| 2560×1440 | 1.25 | 1150×830 |
| 1920×1080 | 1.15 | 1058×764 |
| 1366×768 | 1.0 | 920×664 |
| 1280×720 | 1.0 | 920×664 |
| 800×600 | 0.85 | 782×564 |
| 781×439 | 0.7 | 644×430 |
| 768×432 | 0.7 | 644×423 |
| 640×480 | 0.7 | 644×465 |

The 640×480 row is 4px wider than the screen at the 0.7 floor. `window_height_for` only shortens the height. That width is not one of the screens in finding 5.2.

## 10. Status (third pass, 2026-11-18)

Fixed, not committed. Tests: `tests/test_ui_ux_audit_fixes.py` `ThirdPassTests`; the whole suite is 509 passed.

| Finding | Status |
|---|---|
| 5.1 Catch step | Fixed. The sentence now says Confirm is always needed and Drop / Sell only when the client asks drop or sell first. `drop_ground_pos` is not mentioned. |
| 5.2 Dialogs on a short screen | Fixed. `help_visuals.fit_to_screen` caps the size and the minimum size of the wizard, Fish & items, Timing, the guide and the log to the free screen area, keeping 48px for the title bar. Re-measured with `dialog_fit.py` on Xvfb: at 768×432 / 0.7 the wizard Next, fish Save, timing Save and guide Close end at y≤559 of 616; at 800×600 / 0.85 the log is 925 wide instead of 980. |
| 5.3 Reset Bait while paused | Fixed. The button is enabled while stopped or paused, disabled only while the bots click ("Pause or stop the bots first."). Yes also sets each paused bot's `bait_counter` to its keys × quantity. |
| 5.4 Row pill | Fixed. A chosen idle window says SELECTED, not READY. NOT OPEN and its message survive a mode switch and the end of a run. Refresh now also clears the pill of a row that stayed selected (it used to keep NOT OPEN after Refresh until a new choice). |
| 5.5 Tile names | Fixed. Large zander, Refugee cape, Wise Emperor symbol (and the cape's icon). Actions are stored per file name, so saved choices are unchanged. The German names are left as they are (open question 1). |
| 5.6 Mount / cast key | Fixed. Cast key (Space, A–Z) and Mount key (Ctrl + A–Z) on the Fishing tab, locked while running. The Quick Skip text shows the current key. A saved value outside those lists falls back to the default and is listed in the "reset to defaults" notice. |
| 5.7 Bait notice in Jigsaw | Fixed. The bait notice is put away when Jigsaw is selected and comes back with Fishing, unless Reset Bait was pressed. The first-run welcome notice still shortens the Jigsaw tab once; it is about setup, which applies to Jigsaw too. |
| 5.8 Sound alert label | Fixed: "Sound alert when all windows are out of bait". |
| 5.8 Copy | Fixed: the copy button shows a green check and the tooltip "Copied" for 1.2 s. |
| 5.8 Confirm button name | Fixed: "Set Confirm" in the button, the guide, the Jigsaw bot's messages and the README. |
| 5.8 Focus ring on buttons | Rejected. Qt stylesheets have no `:focus-visible`, so a `QPushButton:focus` rule would also draw the ring after every mouse click on Start, Stop or a pill. |
| 5.8 Sage King Glove wrap | No change (fits, as the report says). |

Open questions 4 (the Rick Roll alert) and 5 (human-like timing on a fresh config) are left to the user; nothing was changed for them.
