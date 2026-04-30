"""
Debug UI windows for the Fishing Bot
StatusLogWindow, IgnoredPositionsWindow, FishDetectorDebugWindow, and InventoryDetectionDebugWindow
"""

import time
import tkinter as tk
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageTk
from mss import mss

from utils import get_resource_path, DEBUG_PRINTS, load_window_icon


class DebugWindow:
    """Shared lifecycle base for all debug visualizer windows.

    Centralises show/hide/destroy and icon loading so subclasses only
    implement _create_window() and (optionally) _update_display().
    """

    def __init__(self, parent):
        self.parent = parent
        self.window: Optional[tk.Toplevel] = None

    def _apply_icon(self) -> None:
        """Applies the application icon to self.window. Call from _create_window()."""
        if self.window:
            load_window_icon(self.window)

    def show(self) -> None:
        """Makes the window visible and brings it to the front."""
        if self.window:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()

    def hide(self) -> None:
        """Hides the window without destroying it."""
        if self.window:
            self.window.withdraw()

    def is_visible(self) -> bool:
        """Returns True if the window is currently shown."""
        if self.window:
            return self.window.winfo_viewable()
        return False

    def _create_header(self, title_text: str) -> None:
        """Creates the standard gold-on-black title bar at the top of self.window."""
        header = tk.Frame(self.window, bg="#000000", height=35)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=title_text, font=("Courier New", 11, "bold"),
                 bg="#000000", fg="#FFD700").pack(pady=6)

    def destroy(self) -> None:
        """Destroys the window and releases the reference."""
        if self.window:
            self.window.destroy()
            self.window = None


class StatusLogWindow(DebugWindow):
    """Separate window for displaying status log messages."""

    def __init__(self, parent):
        super().__init__(parent)
        self.status_text = None
        self._create_window()

    def _create_window(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title("Status Log")
        self.window.geometry("900x500")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(True, True)
        self._apply_icon()
        self._create_header("📋 Status Log")

        content_frame = tk.Frame(self.window, bg="#1a1a1a")
        content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        status_scroll = tk.Scrollbar(content_frame)
        status_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.status_text = tk.Text(content_frame,
                                   bg="#1a1a1a", fg="#00ff00",
                                   font=("Courier", 9),
                                   yscrollcommand=status_scroll.set,
                                   state=tk.DISABLED)
        self.status_text.pack(fill=tk.BOTH, expand=True)
        status_scroll.config(command=self.status_text.yview)

        button_frame = tk.Frame(self.window, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Button(button_frame, text="🗑️ Clear Log",
                  command=self.clear_log,
                  bg="#e74c3c", fg="white",
                  font=("Courier New", 9, "bold"),
                  cursor="hand2", padx=10, pady=3).pack(side=tk.LEFT, padx=5)

        tk.Button(button_frame, text="Close",
                  command=self.hide,
                  bg="#555555", fg="white",
                  font=("Courier New", 9, "bold"),
                  cursor="hand2", padx=15, pady=3).pack(side=tk.RIGHT, padx=5)

        # Hide instead of destroy when user clicks X so the log history is preserved
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.withdraw()

    def add_message(self, message: str):
        """Adds a timestamped message to the status log."""
        if self.status_text:
            self.status_text.config(state=tk.NORMAL)
            timestamp = time.strftime("%H:%M:%S")
            self.status_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self.status_text.see(tk.END)
            self.status_text.config(state=tk.DISABLED)

    def clear_log(self):
        """Clears all messages from the log."""
        if self.status_text:
            self.status_text.config(state=tk.NORMAL)
            self.status_text.delete(1.0, tk.END)
            self.status_text.config(state=tk.DISABLED)


class IgnoredPositionsWindow(DebugWindow):
    """Window displaying ignored inventory positions with 10 px radius circles."""

    def __init__(self, parent, bot_instance):
        super().__init__(parent)
        self.bot = bot_instance
        self.canvas = None
        self.photo_image = None
        self.sct = None  # Own mss instance — mss is not thread-safe to share across threads
        self._create_window()
        self._update_loop_id = None

    def _create_window(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Inventory State - [W{self.bot.bot_id+1}]")
        self.window.geometry("320x400")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)
        self._apply_icon()
        self._create_header("🎯 Inventory State")

        self.counter_label = tk.Label(self.window, text="Ignored: 0 | Empty: 0",
                                      font=("Courier New", 10),
                                      bg="#1a1a1a", fg="#00ff00")
        self.counter_label.pack(pady=3)

        self.canvas = tk.Canvas(self.window, bg="#000000", width=280, height=280,
                                highlightthickness=1, highlightbackground="#333333")
        self.canvas.pack(fill=tk.BOTH, expand=False, padx=5, pady=5)

        # Keep references to prevent Tkinter garbage collection of PhotoImage objects
        self.placeholder_image = None
        self.photo_image = None

        self._draw_placeholder()
        self._schedule_update()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _draw_placeholder(self):
        try:
            # Test pattern lets us confirm the canvas is working before any capture arrives
            placeholder = np.zeros((280, 280, 3), dtype=np.uint8)
            placeholder[:] = (50, 50, 100)
            cv2.rectangle(placeholder, (10, 10), (270, 270), (100, 255, 100), 3)
            cv2.putText(placeholder, "Waiting for", (60, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(placeholder, "inventory...", (50, 160),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            rgb_frame = cv2.cvtColor(placeholder, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.placeholder_image = ImageTk.PhotoImage(pil_image)

            self.canvas.delete("all")
            self.canvas.create_image(140, 140, image=self.placeholder_image, anchor="center")
            self.canvas.update()
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Error drawing placeholder: {e}")
                import traceback
                traceback.print_exc()

    def _schedule_update(self):
        if self.window and self.window.winfo_exists():
            self._update_loop_id = self.window.after(500, self._update_display)

    def _on_close(self):
        self.destroy()

    def _update_display(self):
        try:
            if not self.window or not self.window.winfo_exists():
                return

            if not self.bot.window_manager or not self.bot.window_manager.selected_window:
                if self.placeholder_image:
                    self.canvas.delete("all")
                    self.canvas.create_image(140, 140, image=self.placeholder_image, anchor="center")
                self._schedule_update()
                return

            if self.sct is None:
                self.sct = mss()

            try:
                win_left, win_top, win_width, win_height = self.bot.window_manager.get_window_rect()

                # Capture the right-side inventory strip (same region the bot scans)
                monitor = {
                    "left": win_left + win_width - self.bot._inventory_width,
                    "top": win_top + self.bot._inventory_y_offset,
                    "width": self.bot._inventory_width,
                    "height": max(0, win_height - self.bot._inventory_y_offset - 30)
                }

                sct_img = self.sct.grab(monitor)
                inventory_frame = np.array(sct_img)
                inventory_frame = cv2.cvtColor(inventory_frame, cv2.COLOR_BGRA2BGR)

            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"DEBUG: Manual capture failed: {e}")
                self._schedule_update()
                return

            if inventory_frame is None or not isinstance(inventory_frame, np.ndarray):
                if DEBUG_PRINTS:
                    print(f"DEBUG: Invalid capture returned")
                self._schedule_update()
                return

            inv_h, inv_w = inventory_frame.shape[:2]
            if DEBUG_PRINTS:
                print(f"DEBUG: Captured frame {inv_w}x{inv_h}")

            if inv_h <= 0 or inv_w <= 0:
                if DEBUG_PRINTS:
                    print(f"DEBUG: Invalid dimensions")
                self._schedule_update()
                return

            mean_val = np.mean(inventory_frame)
            if DEBUG_PRINTS:
                print(f"DEBUG: Mean brightness: {mean_val:.1f}")

            if mean_val < 10:
                # Completely black frame means the capture region is wrong or the window is minimised
                if DEBUG_PRINTS:
                    print(f"DEBUG: Image is black, showing test pattern instead")
                test_img = np.zeros((280, 280, 3), dtype=np.uint8)
                test_img[:] = (100, 50, 50)
                cv2.putText(test_img, "Capture failed", (40, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                cv2.putText(test_img, "Check window", (50, 160),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                rgb_test = cv2.cvtColor(test_img, cv2.COLOR_BGR2RGB)
                pil_test = Image.fromarray(rgb_test)
                self.photo_image = ImageTk.PhotoImage(pil_test)
                self.canvas.delete("all")
                self.canvas.create_image(140, 140, image=self.photo_image, anchor="center")
                self.canvas.update()
                self._schedule_update()
                return

            if len(inventory_frame.shape) != 3 or inventory_frame.shape[2] != 3:
                if DEBUG_PRINTS:
                    print(f"DEBUG: Wrong format - shape: {inventory_frame.shape}")
                self._schedule_update()
                return

            viz_frame = inventory_frame.copy()

            # Ignored positions (dead fish / kept items) — red ring
            for ix, iy in self.bot._ignored_positions:
                ix, iy = int(ix), int(iy)
                if 0 <= ix < inv_w and 0 <= iy < inv_h:
                    cv2.circle(viz_frame, (ix, iy), 10, (0, 0, 255), 2)
                    cv2.circle(viz_frame, (ix, iy), 2, (255, 255, 255), -1)

            # Empty slot positions — cyan ring
            for ex, ey in getattr(self.bot, '_empty_slot_positions', []):
                ex, ey = int(ex), int(ey)
                if 0 <= ex < inv_w and 0 <= ey < inv_h:
                    cv2.circle(viz_frame, (ex, ey), 10, (0, 255, 255), 2)
                    cv2.circle(viz_frame, (ex, ey), 2, (0, 200, 200), -1)

            scale = min(280.0 / inv_w, 280.0 / inv_h, 1.0)
            new_w = int(inv_w * scale)
            new_h = int(inv_h * scale)

            if new_w > 0 and new_h > 0:
                viz_resized = cv2.resize(viz_frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                self._schedule_update()
                return

            rgb_frame = cv2.cvtColor(viz_resized, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.photo_image = ImageTk.PhotoImage(pil_image)

            self.canvas.delete("all")
            self.canvas.create_image(140, 140, image=self.photo_image, anchor="center")

            ignored_count = len(self.bot._ignored_positions)
            empty_count = len(getattr(self.bot, '_empty_slot_positions', []))
            self.counter_label.config(text=f"Ignored: {ignored_count} | Empty: {empty_count}")

        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Error updating display: {e}")
                import traceback
                traceback.print_exc()

        self._schedule_update()

    def destroy(self):
        if self._update_loop_id and self.window:
            self.window.after_cancel(self._update_loop_id)
        super().destroy()


class FishDetectorDebugWindow(DebugWindow):
    """Debug window for visualizing fish detection in both minigame and classic fishing modes."""

    def __init__(self, parent, bot_instance):
        super().__init__(parent)
        self.bot = bot_instance
        self.canvas = None
        self.photo_image = None
        self.sct = None  # Own mss instance — mss is not thread-safe to share across threads
        self.status_label = None
        self._create_window()
        self._update_loop_id = None

    def _create_window(self):
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Fish Detector Debug - [W{self.bot.bot_id+1}]")
        self.window.geometry("600x550")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)
        self._apply_icon()
        self._create_header("🎣 Fish Detector Debug")

        info_frame = tk.Frame(self.window, bg="#2a2a2a")
        info_frame.pack(fill=tk.X, padx=5, pady=3)

        info_text = tk.Label(info_frame,
                             text="Window (green) | Fish (red) | Classic fish (magenta) | Click zone (yellow)",
                             font=("Courier New", 8),
                             bg="#2a2a2a", fg="#ffffff",
                             justify=tk.LEFT)
        info_text.pack(anchor="w", padx=5, pady=2)

        self.status_label = tk.Label(self.window, text="Status: Ready",
                                     font=("Courier New", 9),
                                     bg="#1a1a1a", fg="#00ff00")
        self.status_label.pack(pady=2)

        self.canvas = tk.Canvas(self.window, bg="#000000", width=560, height=380,
                                highlightthickness=1, highlightbackground="#333333")
        self.canvas.pack(fill=tk.BOTH, expand=False, padx=5, pady=5)

        info_panel = tk.Frame(self.window, bg="#1a1a1a")
        info_panel.pack(fill=tk.X, padx=5, pady=5)

        self.info_text = tk.Label(info_panel,
                                  text="",
                                  font=("Courier New", 8),
                                  bg="#1a1a1a", fg="#00ff00",
                                  justify=tk.LEFT)
        self.info_text.pack(anchor="w")

        # Keep references to prevent Tkinter garbage collection of PhotoImage objects
        self.placeholder_image = None
        self.photo_image = None

        self._draw_placeholder()
        self._schedule_update()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _draw_placeholder(self):
        try:
            placeholder = np.zeros((380, 560, 3), dtype=np.uint8)
            placeholder[:] = (50, 50, 100)
            cv2.rectangle(placeholder, (10, 10), (550, 370), (100, 255, 100), 3)
            cv2.putText(placeholder, "Waiting for detection...", (80, 180),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

            rgb_frame = cv2.cvtColor(placeholder, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.placeholder_image = ImageTk.PhotoImage(pil_image)

            self.canvas.delete("all")
            self.canvas.create_image(280, 190, image=self.placeholder_image, anchor="center")
            self.canvas.update()
        except Exception:
            pass

    def _schedule_update(self):
        if self.window and self.window.winfo_exists():
            self._update_loop_id = self.window.after(200, self._update_display)

    def _on_close(self):
        self.destroy()

    def _update_display(self):
        """Captures the current game frame and renders mode-appropriate detection overlays."""
        try:
            if not self.window or not self.window.winfo_exists():
                return

            if not self.bot.window_manager or not self.bot.window_manager.selected_window:
                if self.placeholder_image:
                    self.canvas.delete("all")
                    self.canvas.create_image(280, 190, image=self.placeholder_image, anchor="center")
                self.status_label.config(text="Status: No window selected")
                self._schedule_update()
                return

            if self.sct is None:
                self.sct = mss()

            try:
                win_left, win_top, win_width, win_height = self.bot.window_manager.get_window_rect()

                monitor = {
                    "left": win_left,
                    "top": win_top,
                    "width": win_width,
                    "height": win_height
                }

                sct_img = self.sct.grab(monitor)
                frame = np.array(sct_img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            except Exception as e:
                self.status_label.config(text=f"Status: Capture failed - {str(e)[:40]}")
                self._schedule_update()
                return

            if frame is None or frame.size == 0:
                self.status_label.config(text="Status: Invalid frame")
                self._schedule_update()
                return

            viz_frame = frame.copy()
            h, w = frame.shape[:2]

            try:
                status_msg = []
                is_classic_mode = self.bot.config.get('classic_fishing', False)

                if is_classic_mode:
                    cv2.putText(viz_frame, "MODE: CLASSIC", (w - 180, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
                else:
                    cv2.putText(viz_frame, "MODE: MINIGAME", (w - 180, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

                if is_classic_mode:
                    self._draw_classic_overlay(frame, viz_frame, status_msg)
                else:
                    self._draw_minigame_overlay(frame, viz_frame, status_msg)

                self.status_label.config(text=f"Status: {' | '.join(status_msg[:2])}")

            except Exception as e:
                status_msg.clear()
                status_msg.append(f"Detection error: {str(e)[:50]}")
                self.status_label.config(text=f"Status: ERROR - {str(e)[:40]}")

            scale = min(560.0 / w, 380.0 / h, 1.0)
            new_w = int(w * scale)
            new_h = int(h * scale)

            if new_w > 0 and new_h > 0:
                viz_resized = cv2.resize(viz_frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            else:
                self._schedule_update()
                return

            rgb_frame = cv2.cvtColor(viz_resized, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            self.photo_image = ImageTk.PhotoImage(pil_image)

            self.canvas.delete("all")
            self.canvas.create_image(280, 190, image=self.photo_image, anchor="center")

            info_str = " | ".join(status_msg)
            self.info_text.config(text=info_str)

        except Exception:
            self.status_label.config(text="Status: ERROR")

        self._schedule_update()

    def _draw_minigame_overlay(self, frame: np.ndarray, viz_frame: np.ndarray, status_msg: list) -> None:
        """Annotates viz_frame with minigame window bounds and fish position.

        Mirrors the exact detection logic the bot uses so this view shows
        what the bot actually sees during a minigame.
        """
        window_bounds = self.bot.detector.find_fishing_window_bounds(frame)
        window_active, fish_pos = self.bot.detector.detect_window_and_fish(frame)

        if window_bounds:
            x, y, bw, bh = window_bounds
            cv2.rectangle(viz_frame, (x, y), (x + bw, y + bh), (0, 255, 0), 3)
            cv2.putText(viz_frame, f"Window: {bw}x{bh}", (x, y - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            status_msg.append(f"Window bounds: ({x}, {y}) {bw}x{bh}")
        else:
            status_msg.append("Window bounds: NOT FOUND")

        if window_active:
            cv2.putText(viz_frame, "WINDOW ACTIVE", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            status_msg.append("Window active: YES")
        else:
            cv2.putText(viz_frame, "WINDOW INACTIVE", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            status_msg.append("Window active: NO")

        if fish_pos:
            fx, fy = fish_pos
            cv2.circle(viz_frame, (fx, fy), 12, (0, 0, 255), 2)
            cv2.circle(viz_frame, (fx, fy), 3, (255, 255, 255), -1)
            cv2.putText(viz_frame, f"Fish ({fx},{fy})", (fx + 15, fy - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            status_msg.append(f"Fish position: ({fx}, {fy})")
        else:
            status_msg.append("Fish position: NOT DETECTED")

        if self.bot.region and self.bot.region_auto_calibrated:
            cx = self.bot.region.left + self.bot.region.width // 2
            cy = self.bot.region.top + self.bot.region.height // 2
            radius = 67
            cv2.circle(viz_frame, (cx, cy), radius, (255, 255, 0), 2)
            cv2.putText(viz_frame, "Click zone", (cx - 40, cy - radius - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

    def _draw_classic_overlay(self, frame: np.ndarray, viz_frame: np.ndarray, status_msg: list) -> None:
        """Annotates viz_frame with the classic-fish template match result.

        Uses the same multi-scale search region as wait_for_classic_fish() so
        this debug view is a faithful mirror of what the bot actually detects.
        """
        template = self.bot._load_classic_fish_template()
        if template is None:
            cv2.putText(viz_frame, "NO CLASSIC FISH TEMPLATE!", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(viz_frame, "Add assets/fishing/classic_fish.jpg", (20, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            status_msg.append("Classic fish: NO TEMPLATE")
            return

        frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        t_h, t_w = template.shape
        f_h, f_w = frame_gray.shape

        # 250 px wide centred strip, upper half only — same crop as wait_for_classic_fish()
        center_x = f_w // 2
        crop_left = max(0, center_x - 125)
        crop_right = min(f_w, center_x + 125)
        crop_bottom = f_h // 2
        frame_gray_cropped = frame_gray[:crop_bottom, crop_left:crop_right]

        overlay = viz_frame.copy()
        overlay[:, :crop_left] = (overlay[:, :crop_left] * 0.3).astype(np.uint8)
        overlay[:, crop_right:] = (overlay[:, crop_right:] * 0.3).astype(np.uint8)
        overlay[crop_bottom:, :] = (overlay[crop_bottom:, :] * 0.3).astype(np.uint8)
        cv2.line(overlay, (crop_left, 0), (crop_left, crop_bottom), (0, 255, 255), 2)
        cv2.line(overlay, (crop_right, 0), (crop_right, crop_bottom), (0, 255, 255), 2)
        cv2.line(overlay, (crop_left, crop_bottom), (crop_right, crop_bottom), (0, 255, 255), 2)
        cv2.putText(overlay, "Search region (250px, upper, multi-scale)", (crop_left + 5, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

        f_h_cropped, f_w_cropped = frame_gray_cropped.shape

        scales = [0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.25, 2.5, 2.75, 3.0]
        best_match_val = 0
        best_scale = 1.0
        best_loc = (0, 0)
        best_size = (t_w, t_h)

        for scale in scales:
            new_w = int(t_w * scale)
            new_h = int(t_h * scale)

            if new_h > f_h_cropped or new_w > f_w_cropped or new_w < 10 or new_h < 10:
                continue

            scaled_template = cv2.resize(template, (new_w, new_h),
                                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            result = cv2.matchTemplate(frame_gray_cropped, scaled_template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_match_val:
                best_match_val = max_val
                best_scale = scale
                best_loc = max_loc
                best_size = (new_w, new_h)

            if max_val >= 0.8:
                break

        if best_match_val >= 0.7:
            pt1 = (best_loc[0] + crop_left, best_loc[1])
            pt2 = (best_loc[0] + crop_left + best_size[0], best_loc[1] + best_size[1])
            cv2.rectangle(overlay, pt1, pt2, (255, 0, 255), 3)
            cv2.putText(overlay, "CLASSIC FISH DETECTED!",
                        (pt1[0], pt1[1] - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
            cv2.putText(overlay, f"Conf: {best_match_val:.2f}, Scale: {best_scale:.1f}x",
                        (pt1[0], pt1[1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
            status_msg.append(f"Classic fish: DETECTED ({best_match_val:.2f}, {best_scale:.1f}x)")
        else:
            cv2.putText(overlay, "Searching (multi-scale 0.5x-1.5x)...", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (128, 128, 128), 2)
            cv2.putText(overlay, f"Best: {best_match_val:.2f} @ {best_scale:.1f}x (need 0.70)", (20, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 2)
            status_msg.append(f"Classic fish: searching ({best_match_val:.2f})")

        viz_frame[:] = overlay

    def destroy(self):
        if self._update_loop_id and self.window:
            self.window.after_cancel(self._update_loop_id)
        super().destroy()


class InventoryDetectionDebugWindow(DebugWindow):
    """Debug window that shows what items the bot detects in the inventory.

    Displays the captured inventory area with overlaid detection results:
      - Green box + label  : best match (highest confidence)
      - Orange boxes       : other candidates >= 0.60
      - Red circles        : ignored positions (dead fish / already processed)
      - Cyan circles       : known empty slots
    A text panel below shows each candidate's filename, confidence, and action.
    """

    _REFRESH_MS = 1500  # refresh interval

    def __init__(self, parent: tk.Misc, bot):
        super().__init__(parent)
        self.bot = bot
        self._update_loop_id = None
        self._photo = None
        self._sct = mss()
        self._create_window()
        self._schedule_update()

    def _create_window(self):
        bot_id = self.bot.bot_id
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Inventory Detection Debug [W{bot_id + 1}]")
        self.window.geometry("370x650")
        self.window.resizable(True, True)
        self._apply_icon()

        self.canvas = tk.Canvas(self.window, width=340, height=480, bg="black")
        self.canvas.pack(padx=5, pady=5)

        self.text_var = tk.StringVar(value="Waiting for inventory capture…")
        tk.Label(
            self.window,
            textvariable=self.text_var,
            justify=tk.LEFT,
            anchor="nw",
            font=("Courier", 9),
            bg="#1e1e1e",
            fg="#e0e0e0",
            wraplength=360,
        ).pack(fill=tk.X, padx=5, pady=(0, 5))

        self.window.protocol("WM_DELETE_WINDOW", self.hide)

    def _schedule_update(self):
        if self.window:
            self._update_loop_id = self.window.after(self._REFRESH_MS, self._tick)

    def _tick(self):
        if not self.window:
            return
        try:
            self._update_display()
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"[InventoryDetectionDebugWindow] tick error: {e}")
        self._schedule_update()

    def _update_display(self):
        bot = self.bot
        inv_frame = bot.capture_inventory_area()
        if inv_frame is None or inv_frame.size == 0:
            self.text_var.set("No inventory frame captured.")
            return

        overlay = inv_frame.copy()
        inv_h, inv_w = overlay.shape[:2]

        fish_actions = bot.config.get('fish_actions', {})
        ignored = set(bot._ignored_positions)
        empty_slots = list(bot._empty_slot_positions)

        # --- Run every template against the inventory, collect all raw hits >= 0.50 ---
        templates = bot._load_template_cache()
        inventory_gray = cv2.cvtColor(inv_frame, cv2.COLOR_BGR2GRAY)
        RAW_THRESHOLD = 0.70   # only show confident matches
        CLUSTER_DIST = 15      # px — hits this close belong to the same slot

        # raw_hits: list of (filename, cx, cy, confidence)
        raw_hits = []
        for filename, (template, half_w, half_h) in templates.items():
            t_h, t_w = template.shape
            if t_h > inv_h or t_w > inv_w:
                continue
            try:
                result = cv2.matchTemplate(inventory_gray, template, cv2.TM_CCOEFF_NORMED)
                result_copy = result.copy()
                while True:
                    _, max_val, _, max_loc = cv2.minMaxLoc(result_copy)
                    if max_val < RAW_THRESHOLD:
                        break
                    cx = max_loc[0] + half_w
                    cy = max_loc[1] + half_h
                    raw_hits.append((filename, cx, cy, max_val))
                    mx1 = max(0, max_loc[0] - t_w // 2)
                    my1 = max(0, max_loc[1] - t_h // 2)
                    mx2 = min(result_copy.shape[1], max_loc[0] + t_w // 2 + 1)
                    my2 = min(result_copy.shape[0], max_loc[1] + t_h // 2 + 1)
                    result_copy[my1:my2, mx1:mx2] = -1.0
            except Exception:
                continue

        # --- Cluster raw hits into per-slot groups ---
        # Each slot is represented by its centroid (mean of member positions).
        # For each slot we keep ALL template scores so we can show a ranked list.
        slots = []  # list of { 'cx': int, 'cy': int, 'hits': [(fname, conf), ...] }
        for fname, cx, cy, conf in raw_hits:
            matched = None
            for slot in slots:
                if abs(cx - slot['cx']) < CLUSTER_DIST and abs(cy - slot['cy']) < CLUSTER_DIST:
                    matched = slot
                    break
            if matched is None:
                slots.append({'cx': cx, 'cy': cy, 'hits': [(fname, conf)]})
            else:
                matched['hits'].append((fname, conf))
                # Update centroid toward new point (running average)
                n = len(matched['hits'])
                matched['cx'] = (matched['cx'] * (n - 1) + cx) // n
                matched['cy'] = (matched['cy'] * (n - 1) + cy) // n

        # Sort each slot's hits by confidence descending → best match is first
        for slot in slots:
            slot['hits'].sort(key=lambda h: h[1], reverse=True)

        # Sort slots top-to-bottom, left-to-right for the text panel
        slots.sort(key=lambda s: (s['cy'] // 30, s['cx']))

        # --- Draw overlays ---
        BOX = 14

        # Empty slots — cyan circles
        for ex, ey in empty_slots:
            cv2.circle(overlay, (ex, ey), 8, (255, 255, 0), 2)

        # Ignored positions — red circles
        for ix, iy in ignored:
            cv2.circle(overlay, (ix, iy), 10, (0, 0, 255), 2)
            cv2.putText(overlay, "X", (ix - 5, iy + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)

        # Each detected slot — box + best-match label
        for slot in slots:
            cx, cy = slot['cx'], slot['cy']
            best_fname, best_conf = slot['hits'][0]

            is_ignored = any(abs(cx - ix) < CLUSTER_DIST and abs(cy - iy) < CLUSTER_DIST
                             for ix, iy in ignored)

            if is_ignored:
                color = (60, 60, 200)   # dim red-ish — already processed
            elif best_conf >= 0.85:
                color = (0, 220, 0)     # green — high confidence
            else:
                color = (0, 165, 255)   # orange — medium confidence (0.70–0.84)

            cv2.rectangle(overlay, (cx - BOX, cy - BOX), (cx + BOX, cy + BOX), color, 2)
            label = f"{best_fname} {best_conf:.2f}"
            cv2.putText(overlay, label, (max(0, cx - BOX), max(10, cy - BOX - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1)

        # --- Resize overlay to fit canvas (340 wide) ---
        scale = min(340 / max(inv_w, 1), 480 / max(inv_h, 1))
        disp_w = max(1, int(inv_w * scale))
        disp_h = max(1, int(inv_h * scale))
        display = cv2.resize(overlay, (disp_w, disp_h), interpolation=cv2.INTER_NEAREST)
        display_rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(display_rgb)
        self._photo = ImageTk.PhotoImage(image=img)
        self.canvas.config(width=disp_w, height=disp_h)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

        # --- Text panel: one row per slot, ranked by confidence ---
        lines = [f"Slots detected: {len(slots)}  |  ignored: {len(ignored)}  |  empty: {len(empty_slots)}"]
        for slot in slots:
            cx, cy = slot['cx'], slot['cy']
            is_ignored = any(abs(cx - ix) < CLUSTER_DIST and abs(cy - iy) < CLUSTER_DIST
                             for ix, iy in ignored)
            flag = " [IGN]" if is_ignored else ""
            best_fname, best_conf = slot['hits'][0]
            action = fish_actions.get(best_fname, 'keep')
            lines.append(f"({cx:3d},{cy:3d}) {best_fname:<22} {best_conf:.2f}  {action}{flag}")
            # Show runner-up candidates for this slot (up to 3)
            for runner_fname, runner_conf in slot['hits'][1:4]:
                lines.append(f"         ↳ {runner_fname:<22} {runner_conf:.2f}")

        self.text_var.set("\n".join(lines) if lines else "No items detected.")

    def destroy(self):
        if self._update_loop_id and self.window:
            self.window.after_cancel(self._update_loop_id)
        super().destroy()


class JigsawSolverDebugWindow(DebugWindow):
    """Debug window for the jigsaw solver's internal decision stream."""

    _REFRESH_MS = 350

    def __init__(self, parent: tk.Misc, bot):
        super().__init__(parent)
        self.bot = bot
        self._update_loop_id = None
        self._photo = None
        self._events_seen = 0
        self._step_mode = False
        self._create_window()
        self._schedule_update()

    def _create_window(self):
        bot_id = self.bot.bot_id
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Jigsaw Solver Debug [W{bot_id + 1}]")
        self.window.geometry("900x860")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(True, True)
        self._apply_icon()
        self._create_header(f"Jigsaw Solver Debug [W{bot_id + 1}]")

        top = tk.Frame(self.window, bg="#1a1a1a")
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.state_var = tk.StringVar(value="Waiting for jigsaw solver...")
        tk.Label(
            top,
            textvariable=self.state_var,
            justify=tk.LEFT,
            anchor="w",
            font=("Courier New", 9, "bold"),
            bg="#1a1a1a",
            fg="#FFD700",
        ).pack(fill=tk.X)

        # Step-control toolbar
        ctrl = tk.Frame(self.window, bg="#1a1a1a")
        ctrl.pack(fill=tk.X, padx=8, pady=(0, 2))

        self._step_btn = tk.Button(
            ctrl, text="▶ Step", width=10,
            command=self._on_step,
            bg="#2a2a2a", fg="#FFD700", activebackground="#444444",
            font=("Courier New", 9, "bold"), relief=tk.FLAT,
        )
        self._step_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._run_pause_btn = tk.Button(
            ctrl, text="⏸ Pause", width=10,
            command=self._on_toggle_step_mode,
            bg="#2a2a2a", fg="#00ff88", activebackground="#444444",
            font=("Courier New", 9, "bold"), relief=tk.FLAT,
        )
        self._run_pause_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._step_label = tk.Label(
            ctrl, text="Running", bg="#1a1a1a", fg="#888888",
            font=("Courier New", 8),
        )
        self._step_label.pack(side=tk.LEFT)

        self.canvas = tk.Canvas(
            self.window,
            width=864,
            height=420,
            bg="black",
            highlightthickness=1,
            highlightbackground="#333333",
        )
        self.canvas.pack(fill=tk.BOTH, expand=False, padx=8, pady=4)

        lower = tk.PanedWindow(self.window, orient=tk.VERTICAL, bg="#1a1a1a", sashrelief=tk.RAISED)
        lower.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        self.detail_text = tk.Text(
            lower,
            height=9,
            bg="#111111",
            fg="#e0e0e0",
            insertbackground="#e0e0e0",
            font=("Courier New", 9),
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.event_text = tk.Text(
            lower,
            height=12,
            bg="#111111",
            fg="#00ff88",
            insertbackground="#00ff88",
            font=("Courier New", 8),
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        lower.add(self.detail_text)
        lower.add(self.event_text)

        self.window.protocol("WM_DELETE_WINDOW", self.hide)

    def _on_step(self):
        """Signal the bot to execute exactly one iteration."""
        if not self._step_mode:
            self._on_toggle_step_mode()
        getattr(self.bot, "step", lambda: None)()

    def _on_toggle_step_mode(self):
        """Toggle between step-by-step and free-running modes."""
        self._step_mode = not self._step_mode
        getattr(self.bot, "set_step_mode", lambda _: None)(self._step_mode)
        if self._step_mode:
            self._run_pause_btn.config(text="▶ Run", fg="#FFD700")
            self._step_label.config(text="Step mode — click Step to advance")
        else:
            self._run_pause_btn.config(text="⏸ Pause", fg="#00ff88")
            self._step_label.config(text="Running")

    def _schedule_update(self):
        if self.window:
            self._update_loop_id = self.window.after(self._REFRESH_MS, self._tick)

    def _tick(self):
        if not self.window:
            return
        try:
            self._update_display()
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"[JigsawSolverDebugWindow] tick error: {e}")
        self._schedule_update()

    def _update_display(self):
        snapshot = getattr(self.bot, "debug_snapshot", lambda: {})()
        events = getattr(self.bot, "debug_events", lambda: [])()
        self._draw_snapshot(snapshot)
        self._write_detail(snapshot)
        if len(events) != self._events_seen:
            self._write_events(events)
            self._events_seen = len(events)

    def _draw_snapshot(self, snapshot: dict):
        frame = snapshot.get("frame")
        panel_h = 420
        panel_w = 430
        live_w = 430

        solver_panel = self._render_solver_panel(snapshot, panel_w, panel_h)

        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            live = np.zeros((panel_h, live_w, 3), dtype=np.uint8)
            cv2.putText(
                live,
                "Waiting for capture...",
                (40, panel_h // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 215, 255),
                2,
            )
        else:
            overlay = frame.copy()
            grid_bounds = snapshot.get("grid_bounds")
            display_region = None
            if grid_bounds:
                x, y, w, h = grid_bounds
                cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 255, 255), 2)
                cv2.putText(overlay, "grid", (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
                self._draw_grid_cells(overlay, grid_bounds, snapshot)
                display_region = self._grid_focus_region(overlay.shape, grid_bounds, 100)

            slot_centers = snapshot.get("slot_centers") or {}
            available = set(snapshot.get("available_sections") or [])
            missing = set(snapshot.get("missing_sections") or [])
            for section, point in slot_centers.items():
                color = (0, 220, 0) if section in available else (0, 0, 255) if section in missing else (0, 165, 255)
                px, py = int(point[0]), int(point[1])
                cv2.circle(overlay, (px, py), 16, color, 2)
                cv2.putText(overlay, section, (px - 30, py - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)

            target = snapshot.get("decision_target")
            if target:
                tx, ty = int(target[0]), int(target[1])
                cv2.drawMarker(overlay, (tx, ty), (255, 0, 255), cv2.MARKER_CROSS, 22, 2)
                cv2.putText(overlay, "place", (tx + 8, ty - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 0, 255), 1)

            if display_region is not None:
                x1, y1, x2, y2 = display_region
                overlay = overlay[y1:y2, x1:x2]

            live = self._fit_into(overlay, live_w, panel_h)

        cv2.putText(live, "LIVE", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 215, 255), 2)
        cv2.putText(solver_panel, "EXPECTED", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 215, 255), 2)

        divider = np.full((panel_h, 4, 3), 60, dtype=np.uint8)
        combined = np.hstack([live, divider, solver_panel])
        combined_rgb = cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(combined_rgb))
        h, w = combined.shape[:2]
        self.canvas.config(width=w, height=h)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

    def _fit_into(self, img: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        """Resize img preserving aspect, padded into a (target_h, target_w) BGR canvas."""
        h, w = img.shape[:2]
        if h == 0 or w == 0:
            return np.zeros((target_h, target_w, 3), dtype=np.uint8)
        scale = min(target_w / w, target_h / h)
        disp_w = max(1, int(w * scale))
        disp_h = max(1, int(h * scale))
        resized = cv2.resize(img, (disp_w, disp_h), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        oy = (target_h - disp_h) // 2
        ox = (target_w - disp_w) // 2
        canvas[oy:oy + disp_h, ox:ox + disp_w] = resized
        return canvas

    def _render_solver_panel(self, snapshot: dict, width: int, height: int) -> np.ndarray:
        """Render the solver-style abstract board + piece preview the bot expected.

        Mirrors fishing_jigsaw_solver.JigsawApp:
          - filled cells -> gold
          - chosen placement cells -> green
          - empty cells -> dark gray
          - piece figure rendered on the right side
        """
        panel = np.full((height, width, 3), 26, dtype=np.uint8)

        cell = 44
        board_cols, board_rows = 6, 4
        board_w = board_cols * cell
        board_h = board_rows * cell
        margin_x = 18
        board_x = margin_x
        board_y = 50

        filled = set(snapshot.get("filled_cells") or [])
        placement = set(snapshot.get("placement_cells") or [])
        decision_action = snapshot.get("decision_action")

        gold = (0, 215, 255)        # filled (BGR)
        green = (60, 200, 60)       # expected placement
        empty = (60, 60, 60)
        outline = (200, 200, 200)

        for row in range(board_rows):
            for col in range(board_cols):
                idx = row * board_cols + col
                x1 = board_x + col * cell
                y1 = board_y + row * cell
                x2 = x1 + cell
                y2 = y1 + cell
                if idx in filled:
                    color = gold
                elif idx in placement and decision_action == "place":
                    color = green
                else:
                    color = empty
                cv2.rectangle(panel, (x1, y1), (x2, y2), color, -1)
                cv2.rectangle(panel, (x1, y1), (x2, y2), outline, 1)

        # Header text describing what the bot expected
        piece_id = snapshot.get("piece_id") or "?"
        figure_index = snapshot.get("figure_index")
        decision_reason = snapshot.get("decision_reason", "")
        chosen_action = snapshot.get("chosen_action")

        cv2.putText(panel, "Solver board", (board_x, board_y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)

        # Piece preview box (right of the board)
        piece_box_x = board_x + board_w + 20
        piece_box_y = board_y
        piece_cell = 28
        piece_grid = 4  # show 4x4 region (max piece is 4x3)
        piece_box_w = piece_grid * piece_cell
        piece_box_h = piece_grid * piece_cell

        cv2.putText(panel, "Piece", (piece_box_x, piece_box_y - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1)
        cv2.rectangle(panel, (piece_box_x - 2, piece_box_y - 2),
                      (piece_box_x + piece_box_w + 2, piece_box_y + piece_box_h + 2),
                      (90, 90, 90), 1)

        piece_cells = self._piece_cells_for_figure(figure_index)
        for row in range(piece_grid):
            for col in range(piece_grid):
                x1 = piece_box_x + col * piece_cell
                y1 = piece_box_y + row * piece_cell
                x2 = x1 + piece_cell
                y2 = y1 + piece_cell
                if (col, row) in piece_cells:
                    color = (60, 60, 220)  # red
                else:
                    color = (40, 40, 40)
                cv2.rectangle(panel, (x1, y1), (x2, y2), color, -1)
                cv2.rectangle(panel, (x1, y1), (x2, y2), (90, 90, 90), 1)

        # Footer text
        info_y = board_y + board_h + 28
        line_h = 18
        info_lines = [
            f"piece_id : {piece_id}",
            f"figure   : {figure_index if figure_index is not None else '-'}",
            f"action   : {chosen_action if chosen_action is not None else '-'}",
            f"decision : {snapshot.get('decision_action', '-')}",
            f"reason   : {decision_reason[:40]}",
            f"filled   : {len(filled)}/24",
        ]
        for i, line in enumerate(info_lines):
            cv2.putText(
                panel,
                line,
                (margin_x, info_y + i * line_h),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (200, 200, 200),
                1,
            )

        # Legend
        legend_y = height - 22
        cv2.rectangle(panel, (margin_x, legend_y - 10), (margin_x + 14, legend_y + 4), gold, -1)
        cv2.putText(panel, "filled", (margin_x + 20, legend_y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        cv2.rectangle(panel, (margin_x + 90, legend_y - 10), (margin_x + 104, legend_y + 4), green, -1)
        cv2.putText(panel, "expected", (margin_x + 110, legend_y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        cv2.rectangle(panel, (margin_x + 200, legend_y - 10), (margin_x + 214, legend_y + 4), (60, 60, 220), -1)
        cv2.putText(panel, "piece", (margin_x + 220, legend_y + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        return panel

    # (col, row) offsets in the piece's own frame, derived directly from the
    # FIGURES bitmasks in fishing_jigsaw_solver.py. The encoding shifts a cell
    # at (col, row) by (col * N + row), so cells step ROW-first within a column
    # — i.e. FIGURES[1] (0b1110_0000) is a *vertical* line, not horizontal.
    _PIECE_OFFSETS = {
        0: [(0, 0)],                                  # F0 single cell
        1: [(0, 0), (0, 1), (0, 2)],                  # F1 vertical line of 3
        2: [(0, 0), (0, 1), (1, 1)],                  # F2 L-shape
        3: [(0, 0), (1, 0), (1, 1)],                  # F3 Reverse L-shape
        4: [(0, 0), (0, 1), (1, 0), (1, 1)],          # F4 2x2 square
        5: [(0, 0), (1, 0), (1, 1), (2, 1)],          # F5 S-shape
    }

    def _piece_cells_for_figure(self, figure_index):
        if figure_index is None:
            return set()
        try:
            return set(self._PIECE_OFFSETS.get(int(figure_index), []))
        except (TypeError, ValueError):
            return set()

    def _grid_focus_region(self, shape, grid_bounds, border: int):
        frame_h, frame_w = shape[:2]
        x, y, w, h = grid_bounds
        x1 = max(0, x - border)
        y1 = max(0, y - border)
        x2 = min(frame_w, x + w + border)
        y2 = min(frame_h, y + h + border)
        return (x1, y1, x2, y2)

    def _draw_grid_cells(self, overlay: np.ndarray, grid_bounds, snapshot: dict):
        x, y, w, h = grid_bounds
        cell_w = w / 6.0
        cell_h = h / 4.0
        filled = set(snapshot.get("filled_cells") or [])
        placement = set(snapshot.get("placement_cells") or [])
        candidates = snapshot.get("decision_candidates") or []
        best_preview = set(candidates[0].get("cells", [])) if candidates else set()

        for row in range(4):
            for col in range(6):
                idx = row * 6 + col
                x1 = int(round(x + col * cell_w))
                y1 = int(round(y + row * cell_h))
                x2 = int(round(x + (col + 1) * cell_w))
                y2 = int(round(y + (row + 1) * cell_h))
                if idx in placement:
                    color = (255, 0, 255)
                    label = "P"
                elif idx in filled:
                    color = (0, 200, 255)
                    label = "F"
                elif idx in best_preview:
                    color = (180, 80, 255)
                    label = "B"
                else:
                    color = (80, 80, 80)
                    label = ""
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 1)
                if label:
                    cv2.putText(
                        overlay,
                        label,
                        (x1 + 4, y1 + 14),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.42,
                        color,
                        1,
                    )

    def _write_detail(self, snapshot: dict):
        status = snapshot.get("status", "Waiting for jigsaw solver...")
        self.state_var.set(status)
        lines = [
            f"Grid: found={snapshot.get('grid_found')} conf={snapshot.get('grid_confidence', 0.0):.2f} mask={snapshot.get('board_mask', 0):024b}",
            f"Cells: filled={len(snapshot.get('filled_cells') or [])}/24 empty={24 - len(snapshot.get('filled_cells') or [])}",
            f"Slots: available={sorted(snapshot.get('available_sections') or [])} missing={sorted(snapshot.get('missing_sections') or [])} conf={snapshot.get('slot_confidence', {})}",
            f"Inventory page: {snapshot.get('inventory_page', '-')}",
            f"Crate: {snapshot.get('crate_section', '-')} at {snapshot.get('crate_center', '-')}",
            f"Piece: {snapshot.get('piece_id', '-')} figure={snapshot.get('figure_index', '-')} conf={snapshot.get('piece_confidence', 0.0):.2f}",
            f"Decision: {snapshot.get('decision_action', '-')} target={snapshot.get('decision_target', '-')} reason={snapshot.get('decision_reason', '-')}",
            self._format_candidates(snapshot.get("decision_candidates") or [], snapshot.get("chosen_action")),
        ]
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, "\n".join(lines))
        self.detail_text.config(state=tk.DISABLED)

    def _format_candidates(self, candidates, chosen_action):
        if not candidates:
            return "Legal placements: none recorded yet"
        rows = ["Legal placements (lower score is better):"]
        for item in candidates[:8]:
            marker = "*" if item.get("action") == chosen_action else " "
            rows.append(
                f" {marker} action={item.get('action'):>2} offset={item.get('offset')} "
                f"score={item.get('score', 0.0):.3f} cells={item.get('cells')}"
            )
        return "\n".join(rows)

    def _write_events(self, events):
        self.event_text.config(state=tk.NORMAL)
        self.event_text.delete("1.0", tk.END)
        for event in events[-120:]:
            ts = time.strftime("%H:%M:%S", time.localtime(event.get("time", time.time())))
            name = event.get("event", "event")
            detail = event.get("detail", "")
            self.event_text.insert(tk.END, f"[{ts}] {name:<18} {detail}\n")
        self.event_text.see(tk.END)
        self.event_text.config(state=tk.DISABLED)

    def destroy(self):
        if self._update_loop_id and self.window:
            self.window.after_cancel(self._update_loop_id)
        super().destroy()
