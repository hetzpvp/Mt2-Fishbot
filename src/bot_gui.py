"""
BotGUI - Main GUI for the Fishing Bot
Supports up to 8 simultaneous windows
"""

import ctypes
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Dict

import pyautogui
from PIL import Image, ImageTk

try:
    from pynput import keyboard
except ImportError:
    keyboard = None

from utils import get_resource_path, MAX_WINDOWS, DEBUG_MODE_EN, DEBUG_PRINTS
from window_manager import WindowManager
from fishing_bot import FishingBot
from debug_windows import IgnoredPositionsWindow, FishDetectorDebugWindow, StatusLogWindow


class FishSelectionWindow:
    """Window for selecting fish/item actions (keep, drop, open)"""
    
    # Action colors for visual feedback
    ACTION_COLORS = {
        'keep': '#2ecc71',    # Green
        'drop': '#e74c3c',    # Red
        'open': '#3498db',    # Blue
        None: '#555555'       # Gray (not set)
    }
    
    def __init__(self, parent, current_actions: dict, on_save_callback, config: dict = None, accent_color: str = "#FFBB00", rgb_wave_active: bool = False, rgb_wave_hue: int = 0):
        self.parent = parent
        self.current_actions = current_actions.copy()
        self.on_save_callback = on_save_callback
        self.config = config or {}  # Store config for checking drop positions
        self.accent_color = accent_color  # Store dynamic accent color
        self.rgb_wave_active = rgb_wave_active  # RGB wave effect state
        self.rgb_wave_hue = rgb_wave_hue  # Current hue for RGB wave
        self.item_widgets = {}  # {filename: {'frame': frame, 'action_var': var, 'buttons': {}}}
        self.photo_images = []  # Keep references to prevent garbage collection
        self.buttons_to_update = []  # Store button references for RGB wave updates
        
        # Create window
        self.window = tk.Toplevel(parent)
        self.window.title("Fish & Item Selection")
        
        # Calculate window dimensions based on DPI scaling
        base_width = 560
        base_height = 630
        try:
            dpi_scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
            # Scale dimensions proportionally for high DPI
            window_width = int(base_width * max(1.0, dpi_scale * 0.89))
            window_height = int(base_height * max(1.0, dpi_scale * 0.88))
        except Exception:
            window_width = base_width
            window_height = base_height
        
        self.window.geometry(f"{window_width}x{window_height}")
        self.window.configure(bg="#1a1a1a")
        self.window.resizable(False, False)  # Allow vertical resize for DPI scaling
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.window.iconbitmap(icon_path)
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"Error loading icon: {e}")
        
        # Make window modal
        self.window.transient(parent)
        self.window.grab_set()
        
        self.setup_ui()
        self.load_items()
        
        # Start RGB wave if active
        if self.rgb_wave_active:
            self.update_rgb_wave()
        
    def setup_ui(self):
        """Creates the fish selection window UI"""
    
        # Instructions
        instructions_frame = tk.Frame(self.window, bg="#2a2a2a")
        instructions_frame.pack(fill=tk.X, padx=3, pady=0)
        
        instructions = tk.Label(instructions_frame, 
                               text="K=Keep | D=Open&Drop | O=Open ",
                               font=("Courier New", 8),
                               bg="#2a2a2a", fg="#ffffff",
                               justify=tk.CENTER)
        instructions.pack(pady=1)
        
        # Scrollable container
        container = tk.Frame(self.window, bg="#1a1a1a")
        container.pack(fill=tk.BOTH, expand=True, padx=3, pady=1)
        
        # Canvas without scrollbar
        self.canvas = tk.Canvas(container, bg="#1a1a1a", highlightthickness=0)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#1a1a1a")
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Bottom buttons
        button_frame = tk.Frame(self.window, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, padx=5, pady=10)
        
        # Set All buttons
        set_all_frame = tk.Frame(button_frame, bg="#1a1a1a")
        set_all_frame.pack(side=tk.LEFT)
        
        keep_all_btn = tk.Button(set_all_frame, text="Keep All", command=lambda: self.set_all_actions('keep'),
                 bg="#777777", fg=self.accent_color, font=("Courier New", 8),
                 cursor="hand2", padx=3, pady=10)
        keep_all_btn.pack(side=tk.LEFT, padx=2)
        self.buttons_to_update.append(keep_all_btn)
        
        drop_all_btn = tk.Button(set_all_frame, text="Drop All", command=lambda: self.set_all_actions('drop'),
                 bg="#777777", fg=self.accent_color, font=("Courier New", 8),
                 cursor="hand2", padx=3, pady=10)
        drop_all_btn.pack(side=tk.LEFT, padx=2)
        self.buttons_to_update.append(drop_all_btn)
        
        open_all_btn = tk.Button(set_all_frame, text="Open All (Fish)", command=self.set_all_fish_open,
                 bg="#777777", fg=self.accent_color, font=("Courier New", 8),
                 cursor="hand2", padx=3, pady=10)
        open_all_btn.pack(side=tk.LEFT, padx=2)
        self.buttons_to_update.append(open_all_btn)
        
        # Save/Cancel buttons
        save_cancel_frame = tk.Frame(button_frame, bg="#1a1a1a")
        save_cancel_frame.pack(side=tk.RIGHT)
        
        cancel_btn = tk.Button(save_cancel_frame, text="Cancel", command=self.window.destroy,
                 bg="#555555", fg=self.accent_color, font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=10, pady=10)
        cancel_btn.pack(side=tk.LEFT, padx=3)
        self.buttons_to_update.append(cancel_btn)
        
        save_btn = tk.Button(save_cancel_frame, text="Save", command=self.save_and_close,
                bg="#555555", fg=self.accent_color, font=("Courier New", 9, "bold"),
                 cursor="hand2", padx=10, pady=10)
        save_btn.pack(side=tk.LEFT, padx=3)
        self.buttons_to_update.append(save_btn)
    
    def load_items(self):
        """Loads fish and item images from the assets folder"""
        assets_path = get_resource_path("assets")
        
        if not os.path.exists(assets_path):
            tk.Label(self.scrollable_frame, text="Assets folder not found!",
                    bg="#1a1a1a", fg="#e74c3c",
                    font=("Courier New", 12)).pack(pady=20)
            return
        
        # Get all fish and item files
        files = []
        for f in os.listdir(assets_path):
            if f.endswith('_living.jpg') or f.endswith('_living.png'):
                files.append(('fish', f))
            elif f.endswith('_item.jpg') or f.endswith('_item.png'):
                files.append(('item', f))
        
        if not files:
            tk.Label(self.scrollable_frame, text="No fish or item images found in assets folder!",
                    bg="#1a1a1a", fg="#e74c3c",
                    font=("Courier New", 12)).pack(pady=20)
            return
        
        # Sort: fish first, then items
        files.sort(key=lambda x: (0 if x[0] == 'fish' else 1, x[1]))
        
        # Create section labels
        current_type = None
        row = 0
        col = 0
        items_per_row = 7
        
        for item_type, filename in files:
            # Add section header if type changes
            if item_type != current_type:
                if col != 0:
                    row += 1
                    col = 0
                
                section_label = tk.Label(self.scrollable_frame, 
                                        text=f"{'Fish' if item_type == 'fish' else 'Items'}",
                                        font=("Courier New", 9, "bold"),
                                        bg="#1a1a1a", fg="#FFBB00")
                section_label.grid(row=row, column=0, columnspan=items_per_row, sticky="w", pady=(8, 2), padx=3)
                row += 1
                current_type = item_type
            
            # Create item frame
            self.create_item_widget(filename, assets_path, row, col, item_type)
            
            col += 1
            if col >= items_per_row:
                col = 0
                row += 1
    
    def create_item_widget(self, filename: str, assets_path: str, row: int, col: int, item_type: str):
        """Creates a widget for a single fish/item"""
        # Item container
        item_frame = tk.Frame(self.scrollable_frame, bg="#2a2a2a", padx=1, pady=1)
        item_frame.grid(row=row, column=col, padx=1, pady=1, sticky="nsew")
        
        # Load and resize image
        try:
            img_path = os.path.join(assets_path, filename)
            img = Image.open(img_path)
            img = img.resize((36, 36), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.photo_images.append(photo)  # Keep reference
            
            img_label = tk.Label(item_frame, image=photo, bg="#2a2a2a")
            img_label.pack(pady=0)
        except Exception as e:
            # Fallback if image can't be loaded
            img_label = tk.Label(item_frame, text="?", font=("Courier New", 12),
                               bg="#2a2a2a", fg="#888888", width=3, height=1)
            img_label.pack(pady=1)
        
        # Item name (cleaned up)
        name = filename.replace('_living.jpg', '').replace('_living.png', '')
        name = name.replace('_item.jpg', '').replace('_item.png', '')
        name = name.replace('_', ' ')
        # Truncate long names
        if len(name) > 10:
            name = name[:9] + '..'
        
        name_label = tk.Label(item_frame, text=name, font=("Courier New", 7),
                             bg="#2a2a2a", fg="#ffffff")
        name_label.pack(pady=0)
        
        # Action buttons frame (single row layout)
        buttons_frame = tk.Frame(item_frame, bg="#2a2a2a")
        buttons_frame.pack(pady=0)
        
        # Store current action - DEFAULT to 'keep' if not previously set
        current_action = self.current_actions.get(filename, 'keep')
        
        # Create action buttons: Fish get K D O, Items get K D only
        buttons = {}
        if item_type == 'fish':
            button_actions = [('keep', 'K'), ('drop', 'D'), ('open', 'O')]
        else:
            button_actions = [('keep', 'K'), ('drop', 'D')]
        
        for idx, (action, symbol) in enumerate(button_actions):
            btn = tk.Button(buttons_frame, text=symbol, width=3,
                           font=("Courier New", 6, "bold"),
                           cursor="hand2",
                           padx=1, pady=0,
                           command=lambda f=filename, a=action: self.toggle_action(f, a))
            btn.grid(row=0, column=idx, padx=0, pady=0)
            buttons[action] = btn
        
        # Store widget references
        self.item_widgets[filename] = {
            'frame': item_frame,
            'buttons': buttons,
            'current_action': current_action,
            'item_type': item_type
        }
        
        # Make sure default action is saved
        if current_action and filename not in self.current_actions:
            self.current_actions[filename] = current_action
        
        # Update button colors to reflect current action
        self.update_button_colors(filename)
    
    def toggle_action(self, filename: str, action: str):
        """Sets an action for a fish/item (only allows switching to different actions)"""
        widget = self.item_widgets.get(filename)
        if not widget:
            return
        
        # Only change if selecting a different action
        if widget['current_action'] != action:
            widget['current_action'] = action
            self.current_actions[filename] = action
            self.update_button_colors(filename)
    
    def update_button_colors(self, filename: str):
        """Updates button colors based on current action"""
        widget = self.item_widgets.get(filename)
        if not widget:
            return
        
        current = widget['current_action']
        
        for action, btn in widget['buttons'].items():
            if action == current:
                btn.config(bg=self.ACTION_COLORS[action], fg="white", relief=tk.SUNKEN)
            else:
                btn.config(bg="#555555", fg="#aaaaaa", relief=tk.RAISED)
    
    def set_all_actions(self, action: str):
        """Sets the same action for all items"""
        for filename, widget in self.item_widgets.items():
            widget['current_action'] = action
            if action:
                self.current_actions[filename] = action
            else:
                self.current_actions.pop(filename, None)
            self.update_button_colors(filename)
    
    def set_all_fish_open(self):
        """Sets 'open' action for all fish only (items are not affected)"""
        for filename, widget in self.item_widgets.items():
            # Only apply to fish, not items
            if widget['item_type'] == 'fish':
                widget['current_action'] = 'open'
                self.current_actions[filename] = 'open'
                self.update_button_colors(filename)
    
    def save_and_close(self):
        """Saves the current actions and closes the window"""
        # Validate: all items must have an action selected
        items_without_action = [filename for filename, action in self.current_actions.items() if action is None]
        
        if items_without_action:
            messagebox.showwarning("Incomplete Selection", 
                                 "All fish and items must have an action selected!\n\n"
                                 "Fish: Keep, Drop, or Open\n"
                                 "Items: Keep or Drop")
            return
        
        # Check if any item is set to 'drop' and drop positions are not configured
        items_to_drop = [name for name, action in self.current_actions.items() if action == 'drop']
        if items_to_drop:
            drop_pos = self.config.get('drop_button_pos')
            confirm_pos = self.config.get('confirm_button_pos')
            
            # Only show warning if positions are not yet configured
            if not drop_pos or not confirm_pos:
                messagebox.showinfo("WARNING: Please configure button positions!", 
                                   "The fishbot needs to know where to click in order to to drop/sell/destroy items.\n\n"
                                   "Please configure the drop/sell/destroy and the confirm button positions in the\n"
                                   "'Automatic Fish Handling' section before starting the bot.\n\n"
                                   "STEPS TO CONFIGURE:\n"
                                   "1. Drop an item to the floor and don't press anything (only to open the drop/destroy/sell window)\n"
                                   "2. Click 'Set Drop/Sell/Destroy Button Coords' and click on the drop/sell/destroy button in the game\n"
                                   "3. Click 'Set Confirm Button Coords' and click on the confirm button to finalize dropping the item in game\n"
                                   "4. Done! You can now start the bot safely.")
        
        # Call the callback with the actions
        if self.on_save_callback:
            self.on_save_callback(self.current_actions)
        
        # Unbind mousewheel before destroying
        self.canvas.unbind_all("<MouseWheel>")
        
        self.window.destroy()
    
    def update_rgb_wave(self):
        """Updates button colors with RGB wave effect (synced with main GUI)"""
        if not self.rgb_wave_active:
            return
        
        try:
            # Convert HSV to RGB (hue cycles 0-360)
            import colorsys
            h = self.rgb_wave_hue / 360.0
            r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
            
            # Convert to hex color
            color_hex = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
            
            # Update all button foreground colors
            for btn in self.buttons_to_update:
                try:
                    btn.config(fg=color_hex)
                except:
                    pass
            
            # Increment hue for next update
            self.rgb_wave_hue = (self.rgb_wave_hue + 3) % 360
            
            # Schedule next update (60ms for smooth transition)
            if self.rgb_wave_active:
                self.window.after(60, self.update_rgb_wave)
        except:
            pass


class BotGUI:
    """GUI for the fishing bot - supports up to 8 simultaneous windows"""
    
    BOT_VERSION = "1.0.5"  # Version for config validation and GUI display
    ACCENT_COLOR = "#FFBB00"  # Gold color used throughout the GUI
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Fishing Puzzle Player v{self.BOT_VERSION}")
        
        # Calculate window height based on DPI scaling
        base_height = 430
        base_width = 660
        try:
            dpi_scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0
            # Increase height proportionally for high DPI (add extra space)
            window_height = int(base_height * max(1.43, dpi_scale * 1.3))
            window_width = int(base_width * max(1.0, dpi_scale * 0.96))
        except Exception:
            window_height = base_height
            window_width = base_width
        
        self.root.geometry(f"{window_width}x{window_height}")
        self.root.resizable(False, False)  # Allow vertical resize for DPI scaling
        self.root.minsize(660, 430)
        self.root.configure(bg="#000000")
        
        # Try to load and set window icon
        icon_path = get_resource_path("monkey.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"Error loading icon: {e}")
        
        self.window_manager = WindowManager()
        
        # Multi-window support: up to 8 bots
        self.bots: Dict[int, FishingBot] = {}  # bot_id -> FishingBot
        self.bot_threads: Dict[int, threading.Thread] = {}  # bot_id -> Thread
        self.window_managers: Dict[int, WindowManager] = {}  # bot_id -> WindowManager
        self.window_selections: Dict[int, tk.StringVar] = {}  # bot_id -> selected window name
        self.window_stats: Dict[int, dict] = {}  # bot_id -> {hits, games, bait}
        self.ignored_positions_windows: Dict[int, IgnoredPositionsWindow] = {}  # bot_id -> IgnoredPositionsWindow
        self.fish_detector_debug_windows: Dict[int, FishDetectorDebugWindow] = {}  # bot_id -> FishDetectorDebugWindow
        
        # Global keyboard listener for F5 pause
        self.global_key_listener = None
        if keyboard:
            self.global_key_listener = keyboard.Listener(on_press=self.on_global_key_press)
            self.global_key_listener.start()
        
        # Cooldown for button presses (1 second between actions)
        self.last_action_time = 0
        self.action_cooldown = 3.0  # seconds
        self.in_cooldown = False  # Flag to prevent button re-enabling during cooldown
        
        # Flag to prevent sound alert from playing multiple times
        self._sound_alert_played = False
        
        # Config file path in the current working directory
        self.config_file = os.path.join(os.getcwd(), "bot_config.json")
        
        self.config = {
            'version': self.BOT_VERSION,  # Bot version for config validation
            'human_like_clicking': True,
            'quick_skip': True,
            'sound_alert_on_finish': True,
            'classic_fishing': False,
            'classic_fishing_delay': 3.0,  # Delay in seconds after fish detection
            'auto_fish_handling': False,
            'fish_actions': {},  # {filename: 'keep'|'drop'|'open'}
            'drop_button_pos': None,  # (x, y) relative to window - drop/sell button
            'confirm_button_pos': None,  # (x, y) relative to window - confirm button
            'armor_slot_pos': None,  # (x, y) relative to window - armor slot for quick skip
            'accent_color': "#FFBB00",  # Selected accent color
            'rgb_wave_active': False,  # RGB wave effect state
        }
        
        # Bait counter
        self.bait = 800
        
        # Fish selection window reference
        self.fish_selection_window = None
        
        # RGB wave effect state
        self.rgb_wave_active = False
        self.rgb_wave_hue = 0
        
        # Load config from file if it exists
        self.load_config()
        
        self.setup_ui()
        
        # Start RGB wave if it was previously active
        if self.config.get('rgb_wave_active', False):
            self.rgb_wave_active = True
            self.rgb_wave_hue = 0
            self.update_rgb_wave()
        
    def setup_ui(self):
        """Creates the GUI elements"""
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        
        # Try to load and display GIF
        gif_path = get_resource_path("monkey-eating.gif")
        self.photo_images = []
        self.current_frame = 0
        self.gif_label_left = None
        self.gif_label_right = None
        
        if os.path.exists(gif_path):
            try:
                img = Image.open(gif_path)
                # Extract all frames from the GIF
                for frame_index in range(img.n_frames):
                    img.seek(frame_index)
                    frame = img.convert("RGBA")
                    frame.thumbnail((200, 120), Image.Resampling.LANCZOS)
                    self.photo_images.append(ImageTk.PhotoImage(frame))
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"Error loading GIF: {e}")
        
        # Header (always created)
        header = tk.Frame(self.root, bg="#000000", height=100 if self.photo_images else 45)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        
        # Header content frame
        header_content = tk.Frame(header, bg="#000000")
        header_content.pack(pady=5)
        
        # Left GIF (only if loaded)
        if self.photo_images:
            self.gif_label_left = tk.Label(header_content, image=self.photo_images[0], bg="#000000")
            self.gif_label_left.pack(side=tk.LEFT, padx=10)
        
        # Title and Discord info container
        title_container = tk.Frame(header_content, bg="#000000")
        title_container.pack(side=tk.LEFT, padx=10)
        
        # Title (always shown)
        title = tk.Label(title_container, text=f"Fishing Puzzle Player v{self.BOT_VERSION}", 
                        font=("Courier New", 16, "bold"), 
                        bg="#000000", fg=BotGUI.ACCENT_COLOR)
        title.pack(anchor=tk.CENTER)
        
        # Discord info
        discord_label = tk.Label(title_container, text="Discord: boristei", 
                                font=("Courier New", 10), 
                                bg="#000000", fg=BotGUI.ACCENT_COLOR)
        discord_label.pack(anchor=tk.CENTER)
        
        # Right GIF (only if loaded)
        if self.photo_images:
            self.gif_label_right = tk.Label(header_content, image=self.photo_images[0], bg="#000000")
            self.gif_label_right.pack(side=tk.LEFT, padx=10)
            
            # Start GIF animation (only if GIFs loaded)
            self.animate_gif()
        
        # Main container
        main = tk.Frame(self.root, bg="#1a1a1a")
        main.pack(fill=tk.BOTH, expand=True, padx=5, pady=1)
        
        # Top section container - holds windows_frame and side panel
        top_section = tk.Frame(main, bg="#1a1a1a")
        top_section.pack(fill=tk.X, pady=1)
        
        # Multi-Window Selection Section
        windows_frame = tk.LabelFrame(top_section, text="Game Windows (up to 8)", 
                                     font=("Courier New", 10, "bold"),
                                     bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                     padx=5, pady=1)
        windows_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Container for left (combos) and right (refresh button) sections
        windows_container = tk.Frame(windows_frame, bg="#2a2a2a")
        windows_container.pack(fill=tk.X)
        
        # Left section: Window combos
        combos_section = tk.Frame(windows_container, bg="#2a2a2a")
        combos_section.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Window combos storage
        self.window_combos = {}
        self.window_status_labels = {}
        self.window_bait_labels = {}
        self.window_games_labels = {}
        
        # Create 8 window selection rows
        for i in range(MAX_WINDOWS):
            row_frame = tk.Frame(combos_section, bg="#2a2a2a")
            row_frame.pack(fill=tk.X, pady=1)
            
            # Window label
            tk.Label(row_frame, text=f"W{i+1}:", 
                    bg="#2a2a2a", fg="#ffffff",
                    font=("Courier New", 9, "bold")).pack(side=tk.LEFT, padx=2)
            
            # Window selection combo
            self.window_selections[i] = tk.StringVar()
            combo = ttk.Combobox(row_frame, textvariable=self.window_selections[i], 
                                state="readonly", width=32)
            combo.pack(side=tk.LEFT, padx=2)
            # Bind selection change event to update bait display
            combo.bind("<<ComboboxSelected>>", lambda event, idx=i: self.on_window_selected(idx))
            self.window_combos[i] = combo
            
            # Status indicator
            status_label = tk.Label(row_frame, text="⚪", 
                                   bg="#2a2a2a", fg="#888888",
                                   font=("Courier New", 10))
            status_label.pack(side=tk.LEFT, padx=3)
            self.window_status_labels[i] = status_label
            
            # Bait counter
            bait_label = tk.Label(row_frame, text="B:---", 
                                 bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                 font=("Courier New", 8))
            bait_label.pack(side=tk.LEFT, padx=3)
            self.window_bait_labels[i] = bait_label
            
            # Games counter
            games_label = tk.Label(row_frame, text="G:0", 
                                  bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                  font=("Courier New", 8))
            games_label.pack(side=tk.LEFT, padx=3)
            self.window_games_labels[i] = games_label
            
            # Initialize stats
            self.window_stats[i] = {'hits': 0, 'games': 0, 'bait': self.bait}
        
        # Middle section: Refresh button
        refresh_section = tk.Frame(windows_container, bg="#2a2a2a")
        refresh_section.pack(side=tk.LEFT, padx=(5, 0))
        
        self.refresh_windows_btn = tk.Button(refresh_section, text="Refresh\nWindows",
                 command=self.refresh_windows,
                 bg="#666666", fg=BotGUI.ACCENT_COLOR,
                 activebackground="#777777", activeforeground=BotGUI.ACCENT_COLOR,
                 disabledforeground="black",
                 font=("Courier New", 9, "bold"),
                 cursor="hand2",
                 relief=tk.RAISED,
                 borderwidth=2,
                 width=8,
                 padx=5, pady=3)
        self.refresh_windows_btn.pack(padx=10,pady=(8, 0))
        
        # Reset All Bait button
        self.reset_btn = tk.Button(refresh_section,
                                  text="Reset\nClient\nBait",
                                  command=self.reset_bait,
                                  font=("Courier New", 9, "bold"),
                                  bg="#666666", fg=BotGUI.ACCENT_COLOR,
                                  disabledforeground="#000000",
                                  activebackground="#777777",
                                  cursor="hand2",
                                  width=8,
                                  padx=5, pady=3)
        self.reset_btn.pack(padx=10,pady=(8, 0))
        
        # Statistics Section (Total across all windows)
        stats_section_frame = tk.LabelFrame(top_section, text="Total Statistics", 
                                           font=("Courier New", 9, "bold"),
                                           bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                           padx=5, pady=1)
        stats_section_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=(5, 0))
        
        stats_grid = tk.Frame(stats_section_frame, bg="#2a2a2a")
        stats_grid.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # Configure grid to distribute evenly
        stats_grid.grid_rowconfigure(0, weight=1)
        stats_grid.grid_rowconfigure(1, weight=1)
        stats_grid.grid_rowconfigure(2, weight=1)
        stats_grid.grid_rowconfigure(3, weight=1)
        
        # Total games across all windows
        tk.Label(stats_grid, text="Total\nGames", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 8), anchor=tk.W, justify=tk.LEFT).grid(row=0, column=0, sticky=tk.W, pady=2)
        self.total_games_label = tk.Label(stats_grid, text="0", 
                                         bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                         font=("Courier New", 8, "bold"))
        self.total_games_label.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Active windows count
        tk.Label(stats_grid, text="Active\nWindows", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 8), anchor=tk.W, justify=tk.LEFT).grid(row=1, column=0, sticky=tk.W, pady=2)
        self.active_windows_label = tk.Label(stats_grid, text="0", 
                                            bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                            font=("Courier New", 8, "bold"))
        self.active_windows_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Total bait
        tk.Label(stats_grid, text="Total\nbait", 
                bg="#2a2a2a", fg="#ffffff",
                font=("Courier New", 8), anchor=tk.W, justify=tk.LEFT).grid(row=2, column=0, sticky=tk.W, pady=2)
        # Calculate total bait across selected windows only
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label = tk.Label(stats_grid, text=str(total_bait), 
                                  bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                  font=("Courier New", 8, "bold"))
        self.bait_label.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Bait capacity
        self.bait_capacity_text_label = tk.Label(stats_grid, text="", 
                                                 bg="#2a2a2a", fg="#ffffff",
                                                 font=("Courier New", 8), anchor=tk.W, justify=tk.LEFT)
        self.bait_capacity_text_label.grid(row=3, column=0, sticky=tk.W, pady=2)
        
        self.bait_capacity_number_label = tk.Label(stats_grid, text="", 
                                                   bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                                   font=("Courier New", 8, "bold"))
        self.bait_capacity_number_label.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Side Panel - Completely separate frame
        side_panel_frame = tk.LabelFrame(top_section, text="GUI", 
                                        font=("Courier New", 9, "bold"),
                                        bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                        padx=5, pady=5)
        side_panel_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(5, 0))
        
        # Color squares container
        colors_container = tk.Frame(side_panel_frame, bg="#2a2a2a")
        colors_container.pack(pady=5)
        
        # Define colors: current accent, 4 random colors, and gradient placeholder
        color_list = [
            "#FFBB00",  # Current accent color (gold)
            "#e74c3c",  # Red
            "#3498db",  # Blue
            "#04fc6b",  # Green
            "#b14ed8",  # Purple
            "rainbow"   # Placeholder for gradient
        ]
        
        # Create 6 color squares (vertical layout)
        for i, color in enumerate(color_list):
            row = i
            col = 0
            
            if color == "rainbow":
                # Create a gradient canvas for the last square (clickable)
                gradient_canvas = tk.Canvas(colors_container, width=25, height=25, 
                                           bg="#2a2a2a", highlightthickness=1,
                                           highlightbackground="#555555", cursor="hand2")
                gradient_canvas.grid(row=row, column=col, padx=2, pady=2)
                
                # Draw RGB gradient (vertical)
                for y in range(25):
                    # Calculate RGB values for gradient
                    if y < 9:
                        r, g, b = 255, int(255 * y / 8), 0
                    elif y < 17:
                        r, g, b = int(255 * (17 - y) / 8), 255, 0
                    else:
                        r, g, b = 0, int(255 * (25 - y) / 8), int(255 * (y - 17) / 8)
                    
                    color_hex = f"#{r:02x}{g:02x}{b:02x}"
                    gradient_canvas.create_line(0, y, 25, y, fill=color_hex, width=1)
                
                # Make clickable to toggle RGB wave effect
                gradient_canvas.bind("<Button-1>", lambda e: self.toggle_rgb_wave())
            else:
                # Create solid color square (clickable)
                color_frame = tk.Frame(colors_container, bg=color, width=25, height=25,
                                      relief=tk.RAISED, borderwidth=2, cursor="hand2")
                color_frame.grid(row=row, column=col, padx=2, pady=2)
                color_frame.grid_propagate(False)
                
                # Make clickable to change accent color
                color_frame.bind("<Button-1>", lambda e, c=color: self.change_accent_color(c))
        
        # Bot Configuration Section
        config_frame = tk.LabelFrame(main, text="Bot Configuration", 
                                    font=("Courier New", 10, "bold"),
                                    bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                    padx=5, pady=1)
        config_frame.pack(fill=tk.X, pady=(2, 5))
        
        # Main container for side-by-side layout
        config_content = tk.Frame(config_frame, bg="#2a2a2a")
        config_content.pack(fill=tk.X)
        
        # LEFT SECTION: Bot options (Classic Fishing, Human-like, Quick skip, Sound alert)
        left_options_frame = tk.Frame(config_content, bg="#2a2a2a")
        left_options_frame.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 10))
        
        # Classic Fishing checkbox (no minigame) - FIRST option
        self.classic_fishing_var = tk.BooleanVar(value=self.config.get('classic_fishing', False))
        self.classic_fishing_check = tk.Checkbutton(left_options_frame, 
                                              text="Classic Fishing",
                                              variable=self.classic_fishing_var,
                                              command=self.toggle_classic_fishing,
                                              bg="#2a2a2a", fg="#ffffff",
                                              selectcolor="#1a1a1a",
                                              activebackground="#2a2a2a",
                                              font=("Courier New", 9))
        self.classic_fishing_check.pack(anchor=tk.W, pady=1)
        
        # Delay input for classic fishing (below checkbox)
        classic_delay_frame = tk.Frame(left_options_frame, bg="#2a2a2a")
        classic_delay_frame.pack(anchor=tk.W, pady=(0, 1))
        
        tk.Label(classic_delay_frame, text="Delay:", bg="#2a2a2a", fg="#aaaaaa",
                font=("Courier New", 8)).pack(side=tk.LEFT, padx=(0, 2))
        
        self.classic_delay_var = tk.StringVar(value=str(self.config.get('classic_fishing_delay', 3.0)))
        self.classic_delay_entry = tk.Entry(classic_delay_frame, textvariable=self.classic_delay_var,
                                           width=5, bg="#1a1a1a", fg="#00ff00",
                                           font=("Courier New", 9), insertbackground="#00ff00")
        self.classic_delay_entry.pack(side=tk.LEFT)
        self.classic_delay_entry.bind('<FocusOut>', self.update_classic_delay)
        self.classic_delay_entry.bind('<Return>', self.update_classic_delay)
        
        tk.Label(classic_delay_frame, text="sec", bg="#2a2a2a", fg="#aaaaaa",
                font=("Courier New", 8)).pack(side=tk.LEFT, padx=(2, 0))
        
        # Human-like clicking
        self.human_like_var = tk.BooleanVar(value=self.config.get('human_like_clicking', True))
        self.human_like_check = tk.Checkbutton(left_options_frame, 
                                    text="Human-like clicking",
                                    variable=self.human_like_var,
                                    bg="#2a2a2a", fg="#ffffff",
                                    selectcolor="#1a1a1a",
                                    activebackground="#2a2a2a",
                                    disabledforeground="#666666",
                                    font=("Courier New", 9))
        self.human_like_check.pack(anchor=tk.W, pady=1)
        
        # Sound alert checkbox
        self.sound_alert_var = tk.BooleanVar(value=self.config.get('sound_alert_on_finish', True))
        self.sound_alert_check = tk.Checkbutton(left_options_frame, 
                                          text="No bait alert",
                                          variable=self.sound_alert_var,
                                          bg="#2a2a2a", fg="#ffffff",
                                          selectcolor="#1a1a1a",
                                          activebackground="#2a2a2a",
                                          disabledforeground="#666666",
                                          font=("Courier New", 9))
        self.sound_alert_check.pack(anchor=tk.W, pady=1)
        
        # MIDDLE SECTION: Bait Keys Selection
        bait_keys_frame = tk.LabelFrame(config_content, text="Bait Keys (200 bait each)", 
                                        font=("Courier New", 9),
                                        bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                        padx=3, pady=3)
        bait_keys_frame.pack(side=tk.LEFT, anchor=tk.N, padx=(10, 0))
        
        # Get saved bait keys or default to ['1', '2', '3', '4']
        saved_bait_keys = self.config.get('bait_keys', ['1', '2', '3', '4'])
        
        # Main container for keys and right section
        bait_content = tk.Frame(bait_keys_frame, bg="#2a2a2a")
        bait_content.pack(fill=tk.X)
        
        # Left section: Keys
        keys_container = tk.Frame(bait_content, bg="#2a2a2a")
        keys_container.pack(side=tk.LEFT)
        
        # Number keys row
        num_keys_frame = tk.Frame(keys_container, bg="#2a2a2a")
        num_keys_frame.pack(fill=tk.X)
        
        self.bait_key_vars = {}
        self.bait_key_checkboxes = {}  # Store references for enabling/disabling
        for key in ['1', '2', '3', '4']:
            var = tk.BooleanVar(value=key in saved_bait_keys)
            self.bait_key_vars[key] = var
            cb = tk.Checkbutton(num_keys_frame, text=key, variable=var,
                               command=self.update_bait_capacity,
                               bg="#2a2a2a", fg="#ffffff",
                               selectcolor="#1a1a1a",
                               activebackground="#2a2a2a",
                               disabledforeground="#666666",
                               font=("Courier New", 9),
                               width=1)
            cb.pack(side=tk.LEFT, padx=(0, 13),pady=0)
            self.bait_key_checkboxes[key] = cb
        
        # Function keys row
        fn_keys_frame = tk.Frame(keys_container, bg="#2a2a2a")
        fn_keys_frame.pack(fill=tk.X)
        
        for key in ['F1', 'F2', 'F3', 'F4']:
            var = tk.BooleanVar(value=key in saved_bait_keys)
            self.bait_key_vars[key] = var
            cb = tk.Checkbutton(fn_keys_frame, text=key, variable=var,
                               command=self.update_bait_capacity,
                               bg="#2a2a2a", fg="#ffffff",
                               selectcolor="#1a1a1a",
                               activebackground="#2a2a2a",
                               disabledforeground="#666666",
                               font=("Courier New", 9),
                               width=1)
            cb.pack(side=tk.LEFT, padx=(2, 11),pady=0)
            self.bait_key_checkboxes[key] = cb
        
        # Now that bait_key_vars exists, update capacity for the first time
        self.update_bait_capacity()
        
        # RIGHT SECTION: Quick Skip (moved after Bait Keys to be closer)
        quick_skip_frame = tk.LabelFrame(config_content, text="Quick Skip", 
                                        font=("Courier New", 9),
                                        bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                        padx=5, pady=5)
        quick_skip_frame.pack(side=tk.LEFT, anchor=tk.N, padx=(5, 0), fill=tk.BOTH, expand=True)
        
        # Use grid layout for better control
        quick_skip_frame.grid_rowconfigure(0, weight=0)
        quick_skip_frame.grid_rowconfigure(1, weight=0)
        quick_skip_frame.grid_rowconfigure(2, weight=0)
        quick_skip_frame.grid_columnconfigure(0, weight=1)
        quick_skip_frame.grid_columnconfigure(1, weight=0)
        
        # Help button (grid position: top right)
        self.quick_skip_help_btn = tk.Button(quick_skip_frame,
                                             text="❓",
                                             command=self.show_quick_skip_guide,
                                             font=("Courier New", 9),
                                             bg=BotGUI.ACCENT_COLOR, fg="white",
                                             activebackground=BotGUI.ACCENT_COLOR,
                                             cursor="hand2",
                                             padx=8, pady=1)
        self.quick_skip_help_btn.grid(row=0, column=1, sticky=tk.NE, padx=(10, 0), pady=0)
        
        # Quick skip enable checkbox (row 0, column 0)
        self.quick_skip_var = tk.BooleanVar(value=self.config.get('quick_skip', False))
        self.quick_skip_check = tk.Checkbutton(quick_skip_frame, 
                                         text="Enable",
                                         variable=self.quick_skip_var,
                                         command=self.toggle_quick_skip_modes,
                                         bg="#2a2a2a", fg="#ffffff",
                                         selectcolor="#1a1a1a",
                                         activebackground="#2a2a2a",
                                         disabledforeground="#666666",
                                         font=("Courier New", 9))
        self.quick_skip_check.grid(row=0, column=0, sticky=tk.W, pady=0)
        
        # Quick skip mode selection (row 1, column 0)
        quick_skip_modes_frame = tk.Frame(quick_skip_frame, bg="#2a2a2a")
        quick_skip_modes_frame.grid(row=1, column=0, sticky=tk.W, pady=0)
        
        # Mode variables
        current_mode = self.config.get('quick_skip_mode', 'horse')
        self.quick_skip_mode_horse_var = tk.BooleanVar(value=(current_mode == 'horse'))
        self.quick_skip_mode_armor_var = tk.BooleanVar(value=(current_mode == 'armor'))
        
        # Horse mode checkbox
        self.quick_skip_mode_horse_check = tk.Checkbutton(quick_skip_modes_frame,
                                                     text="Horse",
                                                     variable=self.quick_skip_mode_horse_var,
                                                     command=lambda: self.select_quick_skip_mode('horse'),
                                                     bg="#2a2a2a", fg="#ffffff",
                                                     selectcolor="#1a1a1a",
                                                     activebackground="#2a2a2a",
                                                     disabledforeground="#666666",
                                                     font=("Courier New", 8))
        self.quick_skip_mode_horse_check.pack(side=tk.LEFT, padx=(0, 10))
        
        # armor mode checkbox
        self.quick_skip_mode_armor_check = tk.Checkbutton(quick_skip_modes_frame,
                                                      text="Armor",
                                                      variable=self.quick_skip_mode_armor_var,
                                                      command=lambda: self.select_quick_skip_mode('armor'),
                                                      bg="#2a2a2a", fg="#ffffff",
                                                      selectcolor="#1a1a1a",
                                                      activebackground="#2a2a2a",
                                                      disabledforeground="#666666",
                                                      font=("Courier New", 8))
        self.quick_skip_mode_armor_check.pack(side=tk.LEFT)
        
        # Armor slot position setup (row 2, column 0-1, spans both columns)
        armor_slot_frame = tk.Frame(quick_skip_frame, bg="#2a2a2a")
        armor_slot_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(2, 0))
        
        # Armor slot position button
        self.armor_slot_btn = tk.Button(armor_slot_frame,
                                       text="Set Armor Slot Coords",
                                       command=lambda: self.start_position_capture('armor'),
                                       font=("Courier New", 8),
                                       bg="#666666", fg=BotGUI.ACCENT_COLOR,
                                       disabledforeground="#000000",
                                       activebackground="#777777",
                                       cursor="hand2",
                                       state=tk.DISABLED,
                                       padx=4, pady=1,
                                       width=20)
        self.armor_slot_btn.pack(side=tk.LEFT, padx=(0, 3))
        
        # Armor slot position label
        armor_pos = self.config.get('armor_slot_pos')
        armor_pos_text = f"({armor_pos[0]},{armor_pos[1]})" if armor_pos else "Not set"
        self.armor_slot_label = tk.Label(armor_slot_frame, text=armor_pos_text,
                                        bg="#2a2a2a", 
                                        fg="#ffffff" if armor_pos else "#e74c3c",
                                        font=("Courier New", 9),
                                        width=10, anchor=tk.W)
        self.armor_slot_label.pack(side=tk.LEFT)
        
        # Initialize mode checkboxes state based on quick_skip enabled/disabled
        self.toggle_quick_skip_modes()
        
        # Automatic Fish Handling Section
        fish_handling_frame = tk.LabelFrame(config_frame, text="Automatic Fish Handling", 
                                           font=("Courier New", 9),
                                           bg="#2a2a2a", fg=BotGUI.ACCENT_COLOR,
                                           padx=4, pady=1)
        fish_handling_frame.pack(fill=tk.X, pady=(2, 5))
        
        # Main row containing all elements
        fish_handling_row = tk.Frame(fish_handling_frame, bg="#2a2a2a")
        fish_handling_row.pack(fill=tk.X, pady=1)
        
        # LEFT SECTION: Enable checkbox and Select Fishes button (stacked vertically)
        left_section = tk.Frame(fish_handling_row, bg="#2a2a2a")
        left_section.pack(side=tk.LEFT, anchor=tk.W)
        
        # Automatic fish handling checkbox
        self.auto_fish_var = tk.BooleanVar(value=self.config.get('auto_fish_handling', False))
        self.auto_fish_check = tk.Checkbutton(left_section, 
                                        text="Enable",
                                        variable=self.auto_fish_var,
                                        command=self.toggle_auto_fish_handling,
                                        bg="#2a2a2a", fg="#ffffff",
                                        selectcolor="#1a1a1a",
                                        activebackground="#2a2a2a",
                                        disabledforeground="#666666",
                                        font=("Courier New", 9))
        self.auto_fish_check.pack(anchor=tk.W, pady=1)
        
        # Select Fishes button (below checkbox)
        self.select_fishes_btn = tk.Button(left_section,
                                          text="Select Fishes/Items",
                                          command=self.open_fish_selection_window,
                                          font=("Courier New", 8),
                                          bg="#777777", fg=BotGUI.ACCENT_COLOR,
                                          disabledforeground="#000000",
                                          activebackground="#888888",
                                          cursor="hand2",
                                          state=tk.DISABLED,
                                          padx=5, pady=1)
        self.select_fishes_btn.pack(anchor=tk.W, pady=1)
        
        # RIGHT SECTION: Help button
        right_section = tk.Frame(fish_handling_row, bg="#2a2a2a")
        right_section.pack(side=tk.RIGHT, anchor=tk.NE)
        
        # Help button for drop configuration guide (slightly bigger)
        self.drop_help_btn = tk.Button(right_section,
                                       text="❓",
                                       command=self.show_drop_config_guide,
                                       font=("Courier New", 9),
                                       bg=BotGUI.ACCENT_COLOR, fg="white",
                                       activebackground=BotGUI.ACCENT_COLOR,
                                       cursor="hand2",
                                       padx=8, pady=1)
        self.drop_help_btn.pack(pady=0)
        
        # MIDDLE SECTION: Button Coordinates (centered)
        middle_section = tk.Frame(fish_handling_row, bg="#2a2a2a")
        middle_section.pack(expand=True)
        
        # Coordinates container (label on left, buttons on right)
        coords_container = tk.Frame(middle_section, bg="#2a2a2a")
        coords_container.pack(anchor=tk.CENTER)
        
        # Buttons container (on the right)
        buttons_container = tk.Frame(coords_container, bg="#2a2a2a")
        buttons_container.pack(side=tk.LEFT)
        
        # Drop Button Configuration Row
        drop_config_row = tk.Frame(buttons_container, bg="#2a2a2a")
        drop_config_row.pack(pady=1)
        
        # Drop button position capture
        self.drop_btn_pos_btn = tk.Button(drop_config_row,
                                         text="Set Drop/Sell/Destroy Coords",
                                         command=lambda: self.start_position_capture('drop'),
                                         font=("Courier New", 8),
                                         bg="#666666", fg=BotGUI.ACCENT_COLOR,
                                         disabledforeground="#000000",
                                         activebackground="#777777",
                                         cursor="hand2",
                                         state=tk.DISABLED,
                                         padx=4, pady=1,
                                         width=28)
        self.drop_btn_pos_btn.pack(side=tk.LEFT, padx=(0, 3))
        
        # Drop button position label
        drop_pos = self.config.get('drop_button_pos')
        drop_pos_text = f"({drop_pos[0]},{drop_pos[1]})" if drop_pos else "Not set"
        self.drop_btn_pos_label = tk.Label(drop_config_row, text=drop_pos_text,
                                          bg="#2a2a2a", 
                                          fg="#ffffff" if drop_pos else "#e74c3c",
                                          font=("Courier New", 9),
                                          width=10, anchor=tk.W)
        self.drop_btn_pos_label.pack(side=tk.LEFT)
        
        # Confirm Button Configuration Row
        confirm_config_row = tk.Frame(buttons_container, bg="#2a2a2a")
        confirm_config_row.pack(pady=1)
        
        # Confirm button position capture
        self.confirm_btn_pos_btn = tk.Button(confirm_config_row,
                                            text="Set Confirm Button Coords",
                                            command=lambda: self.start_position_capture('confirm'),
                                            font=("Courier New", 8),
                                            bg="#666666", fg=BotGUI.ACCENT_COLOR,
                                            disabledforeground="#000000",
                                            activebackground="#777777",
                                            cursor="hand2",
                                            state=tk.DISABLED,
                                            padx=4, pady=1,
                                            width=28)
        self.confirm_btn_pos_btn.pack(side=tk.LEFT, padx=(0, 3))
        
        # Confirm button position label
        confirm_pos = self.config.get('confirm_button_pos')
        confirm_pos_text = f"({confirm_pos[0]},{confirm_pos[1]})" if confirm_pos else "Not set"
        self.confirm_btn_pos_label = tk.Label(confirm_config_row, text=confirm_pos_text,
                                             bg="#2a2a2a", 
                                             fg="#ffffff" if confirm_pos else "#e74c3c",
                                             font=("Courier New", 9),
                                             width=10, anchor=tk.W)
        self.confirm_btn_pos_label.pack(side=tk.LEFT)
        
        # Position capture state
        self._position_capture_mode = None  # None, 'drop', or 'confirm'
        self._position_capture_window = None  # Store the target window name
        self._position_capture_listener = None
        
        # Update button state based on checkbox
        self.toggle_auto_fish_handling()
        
        # Update human-like clicking state based on classic fishing (no warning on startup)
        self.toggle_classic_fishing(show_warning=False)
        
        # Create separate status log window (only if DEBUG_MODE_EN is true)
        self.status_log_window = None
        if DEBUG_MODE_EN:
            self.status_log_window = StatusLogWindow(self.root)
            self.status_log_window.show()  # Show it by default in debug mode
        
        # Control Buttons Section
        button_frame = tk.Frame(main, bg="#1a1a1a")
        button_frame.pack(fill=tk.X, pady=1)
        
        # Start/Pause button (combines start, pause, resume functionality)
        self.start_pause_btn = tk.Button(button_frame, 
                                       text="▶ Start All",
                                       command=self.start_or_pause_bots,
                                       font=("Courier New", 13, "bold"),
                                       bg="#888888", fg=BotGUI.ACCENT_COLOR,
                                       disabledforeground="#000000",
                                       activebackground="#999999",
                                       cursor="hand2",
                                       state=tk.NORMAL,
                                       padx=40, pady=4)
        self.start_pause_btn.pack(side=tk.LEFT, expand=True, padx=3,pady=4)
        
        # Stop All button
        self.stop_all_btn = tk.Button(button_frame, 
                                      text="⏹ Stop All",
                                      command=self.stop_all_bots,
                                      font=("Courier New", 13, "bold"),
                                      bg="#888888", fg=BotGUI.ACCENT_COLOR,
                                      disabledforeground="#000000",
                                      activebackground="#999999",
                                      cursor="hand2",
                                      state=tk.DISABLED,
                                      padx=40, pady=4)
        self.stop_all_btn.pack(side=tk.LEFT, expand=True, padx=3,pady=4)
        
        self.add_status("Welcome! Select up to 8 windows and click Start All to begin.")
        self.add_status("Press F5 to pause/resume all bots.")
        
        # Refresh windows list after UI is fully initialized
        self.refresh_windows()
        
        # Restore previously selected windows if they exist
        if self.previous_windows:
            try:
                current_windows = set(self.window_combos[0]['values'])
                for i, prev_win in enumerate(self.previous_windows):
                    if i < MAX_WINDOWS and prev_win and prev_win in current_windows:
                        self.window_selections[i].set(prev_win)
                        # Update bait label for restored window
                        self.window_stats[i]['bait'] = self.bait
                        self.window_bait_labels[i].config(text=f"B:{self.bait}")
                        self.add_status(f"Restored window {i+1}: {prev_win}")
                # Update total bait label after restoring windows
                total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
                self.bait_label.config(text=str(total_bait))
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"Error restoring window selection: {e}")
        
        # Donations Section (at the very bottom)
        donations_frame = tk.Frame(self.root, bg="#000000")
        donations_frame.pack(fill=tk.X, side=tk.BOTTOM)
        
        donations_text_frame = tk.Frame(donations_frame, bg="#000000")
        donations_text_frame.pack(pady=2)
        
        # Try to load BTC icon
        btc_icon_path = get_resource_path("btc_icon.png")
        if DEBUG_PRINTS:
            print(f"Looking for BTC icon at: {btc_icon_path}")
            print(f"BTC icon exists: {os.path.exists(btc_icon_path)}")
        
        if os.path.exists(btc_icon_path):
            try:
                btc_img = Image.open(btc_icon_path)
                # Resize icon to small size (14x14)
                btc_img = btc_img.resize((14, 14), Image.Resampling.LANCZOS)
                self.btc_icon_photo = ImageTk.PhotoImage(btc_img)
                
                # Create icon label
                btc_icon_label = tk.Label(donations_text_frame, image=self.btc_icon_photo, bg="#000000")
                btc_icon_label.pack(side=tk.LEFT, padx=(1, 1))
                if DEBUG_PRINTS:
                    print("BTC icon loaded successfully!")
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"Error loading BTC icon: {e}")
                import traceback
                traceback.print_exc()
        else:
            if DEBUG_PRINTS:
                print(f"BTC icon not found at {btc_icon_path}")
        
        self.btc_address = "3AGrrTf1v9QZsMPEoezYTRbf9JyW4nQtHu"
        self.donations_label = tk.Label(donations_text_frame, 
                                  text=f"Donations: {self.btc_address}",
                                  font=("Courier New", 9),
                                  bg="#000000", fg=BotGUI.ACCENT_COLOR,
                                  wraplength=600, justify=tk.CENTER)
        self.donations_label.pack(side=tk.LEFT, padx=3)
        
        copy_btn = tk.Button(donations_text_frame,
                            text="📋",
                            command=self.copy_btc_address,
                            font=("Courier New", 10),
                            bg="#000000", fg=BotGUI.ACCENT_COLOR,
                            activebackground="#1a1a1a", activeforeground=BotGUI.ACCENT_COLOR,
                            relief=tk.FLAT,
                            cursor="hand2",
                            padx=3, pady=1)
        copy_btn.pack(side=tk.LEFT, padx=2)
    
    def load_config(self):
        """
        Loads configuration from the config file if it exists.
        Validates version - if version is missing or different, config is recreated.
        Restores human_like_clicking, quick_skip, bait counter, bait keys, and window selections.
        """
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    saved_config = json.load(f)
                    
                    # Check version - if missing or different, treat as invalid and skip loading
                    saved_version = saved_config.get('version')
                    if saved_version != self.BOT_VERSION:
                        if DEBUG_PRINTS:
                            print(f"Config version mismatch (saved: {saved_version}, current: {self.BOT_VERSION}). Recreating config.")
                        self.previous_windows = []
                        return
                    
                    # Restore config settings
                    if 'human_like_clicking' in saved_config:
                        self.config['human_like_clicking'] = saved_config['human_like_clicking']
                    if 'quick_skip' in saved_config:
                        self.config['quick_skip'] = saved_config['quick_skip']
                    if 'quick_skip_mode' in saved_config:
                        self.config['quick_skip_mode'] = saved_config['quick_skip_mode']
                    if 'sound_alert_on_finish' in saved_config:
                        self.config['sound_alert_on_finish'] = saved_config['sound_alert_on_finish']
                    if 'classic_fishing' in saved_config:
                        self.config['classic_fishing'] = saved_config['classic_fishing']
                    if 'classic_fishing_delay' in saved_config:
                        self.config['classic_fishing_delay'] = saved_config['classic_fishing_delay']
                    # Restore bait keys
                    if 'bait_keys' in saved_config:
                        self.config['bait_keys'] = saved_config['bait_keys']
                    # Restore bait counter
                    if 'bait' in saved_config:
                        self.bait = saved_config['bait']
                    # Restore auto fish handling settings
                    if 'auto_fish_handling' in saved_config:
                        self.config['auto_fish_handling'] = saved_config['auto_fish_handling']
                    if 'fish_actions' in saved_config:
                        self.config['fish_actions'] = saved_config['fish_actions']
                    # Restore drop button positions
                    if 'drop_button_pos' in saved_config:
                        self.config['drop_button_pos'] = saved_config['drop_button_pos']
                    if 'confirm_button_pos' in saved_config:
                        self.config['confirm_button_pos'] = saved_config['confirm_button_pos']
                    if 'armor_slot_pos' in saved_config:
                        self.config['armor_slot_pos'] = saved_config['armor_slot_pos']
                    # Restore accent color
                    if 'accent_color' in saved_config:
                        self.config['accent_color'] = saved_config['accent_color']
                        BotGUI.ACCENT_COLOR = saved_config['accent_color']
                    # Restore RGB wave state
                    if 'rgb_wave_active' in saved_config:
                        self.config['rgb_wave_active'] = saved_config['rgb_wave_active']
                    # Store previously selected windows for later restoration (multi-window)
                    self.previous_windows = saved_config.get('selected_windows', [])
                    # Also support legacy single window
                    if not self.previous_windows and saved_config.get('selected_window'):
                        self.previous_windows = [saved_config.get('selected_window')]
            except Exception as e:
                if DEBUG_PRINTS:
                    print(f"Error loading config: {e}")
                self.previous_windows = []
        else:
            self.previous_windows = []
    
    def save_config(self):
        """
        Saves current configuration to the config file.
        Always saves the bot version for validation on next load.
        """
        try:
            # Get selected bait keys
            selected_bait_keys = self.get_selected_bait_keys() if hasattr(self, 'bait_key_vars') else ['1', '2', '3', '4']
            
            # Get all selected windows
            selected_windows = []
            if hasattr(self, 'window_selections'):
                for i in range(MAX_WINDOWS):
                    win_name = self.window_selections[i].get() if i in self.window_selections else ""
                    selected_windows.append(win_name)
            
            config_data = {
                'version': self.BOT_VERSION,  # Always save current version
                'human_like_clicking': self.config.get('human_like_clicking', True),
                'quick_skip': self.config.get('quick_skip', False),
                'quick_skip_mode': self.config.get('quick_skip_mode', 'horse'),
                'sound_alert_on_finish': self.config.get('sound_alert_on_finish', True),
                'classic_fishing': self.config.get('classic_fishing', False),
                'classic_fishing_delay': self.config.get('classic_fishing_delay', 3.0),
                'auto_fish_handling': self.config.get('auto_fish_handling', False),
                'fish_actions': self.config.get('fish_actions', {}),
                'drop_button_pos': self.config.get('drop_button_pos'),
                'confirm_button_pos': self.config.get('confirm_button_pos'),
                'armor_slot_pos': self.config.get('armor_slot_pos'),
                'accent_color': BotGUI.ACCENT_COLOR,
                'rgb_wave_active': self.rgb_wave_active,
                'bait_keys': selected_bait_keys,
                'bait': self.bait,
                'selected_windows': selected_windows,
                'selected_window': selected_windows[0] if selected_windows else None  # Legacy support
            }
            with open(self.config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
        except Exception as e:
            if DEBUG_PRINTS:
                print(f"Error saving config: {e}")
    
    def get_selected_bait_keys(self) -> list:
        """Returns list of selected bait keys in order."""
        key_order = ['1', '2', '3', '4', 'F1', 'F2', 'F3', 'F4']
        return [key for key in key_order if self.bait_key_vars.get(key, tk.BooleanVar(value=False)).get()]
    
    def get_max_bait_capacity(self) -> int:
        """Returns max bait capacity based on selected keys (200 per key)."""
        return len(self.get_selected_bait_keys()) * 200
    
    def update_bait_capacity(self):
        """Updates the bait capacity label based on selected keys."""
        selected_keys = self.get_selected_bait_keys()
        capacity = len(selected_keys) * 200
        if capacity > 0:
            self.bait_capacity_text_label.config(text="Bait\nper\nclient")
            self.bait_capacity_number_label.config(
                text=str(capacity),
                fg=BotGUI.ACCENT_COLOR
            )
            # Reset bait to new capacity
            self.bait = capacity
            
            # Update bait counters and labels for all windows
            for i in range(MAX_WINDOWS):
                self.window_stats[i]['bait'] = capacity
                # Update label - show capacity if selected, otherwise show B:---
                is_selected = self.window_selections[i].get()
                if is_selected:
                    self.window_bait_labels[i].config(text=f"B:{capacity}")
                else:
                    self.window_bait_labels[i].config(text="B:---")
            
            # Update total bait label with sum of selected windows only
            total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
            self.bait_label.config(text=str(total_bait))
            
            self.save_config()
        else:
            self.bait_capacity_text_label.config(text="Bait\nper\nclient")
            self.bait_capacity_number_label.config(
                text=str(capacity),
                fg="#e74c3c"
            )
            self.bait_label.config(text="0")
            # Reset all window bait displays - selected windows show B:0, unselected show B:---
            for i in range(MAX_WINDOWS):
                self.window_stats[i]['bait'] = 0
                is_selected = self.window_selections[i].get()
                self.window_bait_labels[i].config(text="B:0" if is_selected else "B:---")
            self.save_config()
        
    def refresh_windows(self):
        """Refreshes the list of available windows for all window combos"""
        try:
            windows = WindowManager.get_all_windows()
            window_names = [name for name, _ in windows]
            
            # Add empty option at the start to allow unselecting
            window_names_with_empty = [""] + window_names
            window_names_set = set(window_names)
            
            # Update all window combos and preserve current selections if still available
            for i in range(MAX_WINDOWS):
                current_sel = self.window_selections[i].get()
                self.window_combos[i]['values'] = window_names_with_empty
                # Restore selection if it's still available
                if current_sel and current_sel in window_names_set:
                    self.window_selections[i].set(current_sel)
            
            if window_names:
                self.add_status(f"Found {len(window_names)} visible window(s)")
            else:
                self.add_status("No visible windows found")
        except Exception as e:
            self.add_status(f"Error getting windows: {e}")
        
    def add_status(self, message: str):
        """
        Adds a status message to the status log window.
        
        Args:
            message: The status message to display
        """
        if not DEBUG_MODE_EN or not hasattr(self, 'status_log_window') or not self.status_log_window:
            return
        self.status_log_window.add_message(message)
    
    def toggle_log_visibility(self):
        """Toggles the visibility of the status log window."""
        if self.status_log_window is None:
            return
        if self.show_log_var.get():
            self.status_log_window.show()
        else:
            self.status_log_window.hide()
    
    def toggle_quick_skip_modes(self):
        """Enables or disables the quick skip mode checkboxes based on the main quick skip checkbox."""
        enabled = self.quick_skip_var.get()
        state = tk.NORMAL if enabled else tk.DISABLED
        
        self.quick_skip_mode_horse_check.config(state=state)
        self.quick_skip_mode_armor_check.config(state=state)
        
        # Armor slot button should only be enabled if quick_skip is enabled AND armor mode is selected
        armor_selected = self.quick_skip_mode_armor_var.get()
        armor_btn_state = tk.NORMAL if (enabled and armor_selected) else tk.DISABLED
        self.armor_slot_btn.config(state=armor_btn_state)
        
        # Save config
        self.config['quick_skip'] = enabled
        self.save_config()
    
    def select_quick_skip_mode(self, mode: str):
        """Selects a quick skip mode and unchecks the other mode (mutually exclusive)."""
        if mode == 'horse':
            self.quick_skip_mode_horse_var.set(True)
            self.quick_skip_mode_armor_var.set(False)
            self.config['quick_skip_mode'] = 'horse'
            
            # Disable armor slot button when horse mode is selected
            self.armor_slot_btn.config(state=tk.DISABLED)
            
            # Show warning for horse mode
            messagebox.showinfo("Quick Skip - Horse Mode", 
                               "This mode uses CTRL+G to quickly skip the fishing animation.\n\n"
                               "HOW IT WORKS:\n"
                               "• After catching a fish, the bot presses CTRL+G twice\n"
                               "REQUIREMENTS:\n"
                               "• You must have a horse in your inventory\n"
                               "• CTRL+G must be bound to mount/dismount horse in your game settings\n\n"
                               "TIP: This is the default and most commonly used quick skip method.")
        else:  # armor
            self.quick_skip_mode_armor_var.set(True)
            self.quick_skip_mode_horse_var.set(False)
            self.config['quick_skip_mode'] = 'armor'
            
            # Enable armor slot button when armor mode is selected (if quick_skip is enabled)
            if self.quick_skip_var.get():
                self.armor_slot_btn.config(state=tk.NORMAL)
            
            # Show warning for armor mode with setup instructions
            messagebox.showinfo("Quick Skip - Armor Mode", 
                               "This mode right-clicks on your armor slot to quickly skip the fishing animation.\n\n"
                               "HOW IT WORKS:\n"
                               "• After catching a fish, the bot right-clicks on your armor slot\n"
                               "STEPS TO CONFIGURE:\n"
                               "1. Make sure your character has armor equipped\n"
                               "2. Click 'Set Armor Slot Coords' button\n"
                               "3. Click on the armor slot in your game inventory\n"
                               "4. Done! You can now start the bot with armor quick skip enabled.")
        self.save_config()
    
    def toggle_classic_fishing(self, show_warning: bool = True):
        """Toggles the classic fishing mode and disables human-like clicking when enabled.
        show_warning: If True, shows warning message when enabling. Set to False when loading from config."""
        enabled = self.classic_fishing_var.get()
        self.config['classic_fishing'] = enabled
        
        if enabled:
            # Show warning message about classic fishing mode (only if user clicked, not on config load)
            if show_warning:
                messagebox.showwarning(
                    "Classic Fishing Mode",
                    "Classic Fishing Mode\n\n"
                    "This mode only works with the OLD Metin2 fishing system!\n\n"
                    "It will NOT work with the minigame fishing system.\n"
                    "Make sure your server uses the classic fishing mechanics."
                )
            # Disable human-like clicking when classic fishing is enabled
            self.human_like_check.config(state=tk.DISABLED)
            self.classic_delay_entry.config(state=tk.NORMAL)
        else:
            # Re-enable human-like clicking when classic fishing is disabled
            self.human_like_check.config(state=tk.NORMAL)
            # Disable delay entry when classic fishing is disabled
            self.classic_delay_entry.config(state=tk.DISABLED)
        
        self.save_config()
    
    def update_classic_delay(self, event=None):
        """Updates the classic fishing delay from the entry field."""
        try:
            delay = float(self.classic_delay_var.get())
            if delay < 0:
                delay = 0
            elif delay > 30:
                delay = 30  # Max 30 seconds
            self.config['classic_fishing_delay'] = delay
            self.classic_delay_var.set(str(delay))
            self.save_config()
            
            # Update running bots with new delay value
            for bot_id, bot in self.bots.items():
                if bot and bot.running:
                    bot.config['classic_fishing_delay'] = delay
        except ValueError:
            # Reset to current config value if invalid
            self.classic_delay_var.set(str(self.config.get('classic_fishing_delay', 3.0)))
    
    def toggle_auto_fish_handling(self):
        """Toggles the automatic fish handling feature and updates button state."""
        enabled = self.auto_fish_var.get()
        self.config['auto_fish_handling'] = enabled
        
        if enabled:
            self.select_fishes_btn.config(state=tk.NORMAL)
        else:
            self.select_fishes_btn.config(state=tk.DISABLED)
        
        # Update drop button states based on whether any fish is set to 'drop'
        self._update_drop_buttons_state()
        
        self.save_config()
    
    def show_drop_config_guide(self):
        """Shows the drop configuration guide message."""
        messagebox.showinfo("Drop Configuration Guide", 
                           "The fishbot needs to know where to click in order to drop/sell/destroy items.\n\n"
                           "Please configure the drop/sell/destroy and the confirm button positions in the\n"
                           "'Automatic Fish Handling' section before starting the bot.\n\n"
                           "STEPS TO CONFIGURE:\n"
                           "1. Drop an item to the floor and don't press anything (only to open the drop/destroy/sell window)\n"
                           "2. Click 'Set Drop/Sell/Destroy Button Coords' and click on the drop/sell/destroy button in the game\n"
                           "3. Click 'Set Confirm Button Coords' and click on the confirm button to finalize dropping the item in game\n"
                           "4. Done! You can now start the bot safely.")
    
    def show_quick_skip_guide(self):
        """Shows the quick skip guide message based on selected mode."""
        # Determine which mode is selected
        if self.quick_skip_mode_horse_var.get():
            messagebox.showinfo("Quick Skip - Horse Mode", 
                               "This mode uses CTRL+G to quickly skip the fishing animation.\n\n"
                               "HOW IT WORKS:\n"
                               "• After catching a fish, the bot presses CTRL+G twice\n"
                               "REQUIREMENTS:\n"
                               "• You must have a horse in your inventory\n"
                               "• CTRL+G must be bound to mount/dismount horse in your game settings\n\n"
                               "TIP: This is the default and most commonly used quick skip method.")
        else:
            messagebox.showinfo("Quick Skip - Armor Mode", 
                               "This mode right-clicks on your armor slot to quickly skip the fishing animation.\n\n"
                               "HOW IT WORKS:\n"
                               "• After catching a fish, the bot right-clicks on your armor slot\n"
                               "STEPS TO CONFIGURE:\n"
                               "1. Make sure your character has armor equipped\n"
                               "2. Click 'Set Armor Slot Coords' button\n"
                               "3. Click on the armor slot in your game inventory\n"
                               "4. Done! You can now start the bot with armor quick skip enabled.")
                
    def _update_drop_buttons_state(self):
        """Enables drop position buttons only if any fish/item is set to 'drop' action."""
        fish_actions = self.config.get('fish_actions', {})
        has_drop_action = any(action == 'drop' for action in fish_actions.values())
        
        if has_drop_action and self.auto_fish_var.get():
            if hasattr(self, 'drop_btn_pos_btn'):
                self.drop_btn_pos_btn.config(state=tk.NORMAL)
            if hasattr(self, 'confirm_btn_pos_btn'):
                self.confirm_btn_pos_btn.config(state=tk.NORMAL)
        else:
            if hasattr(self, 'drop_btn_pos_btn'):
                self.drop_btn_pos_btn.config(state=tk.DISABLED)
            if hasattr(self, 'confirm_btn_pos_btn'):
                self.confirm_btn_pos_btn.config(state=tk.DISABLED)
    
    def change_accent_color(self, new_color: str, from_rgb_wave: bool = False):
        """Changes the accent color throughout the GUI."""
        # Stop RGB wave effect if it's running (but not if called from RGB wave itself)
        if not from_rgb_wave:
            self.rgb_wave_active = False
        
        # Update the class constant
        BotGUI.ACCENT_COLOR = new_color
        
        # Update all LabelFrame titles and Labels recursively
        def update_widget_colors(widget):
            # Update LabelFrame foreground
            if isinstance(widget, tk.LabelFrame):
                try:
                    widget.config(fg=new_color)
                except:
                    pass
            # Update Label foreground for title and discord labels
            elif isinstance(widget, tk.Label):
                try:
                    text = str(widget.cget('text'))
                    if 'Fishing Puzzle Player' in text or 'Discord:' in text or 'BTC' in text:
                        widget.config(fg=new_color)
                except:
                    pass
            # Update Button foreground for copy button
            elif isinstance(widget, tk.Button):
                try:
                    if widget.cget('text') == "📋":
                        widget.config(fg=new_color, activeforeground=new_color)
                except:
                    pass
            
            # Recursively update children
            try:
                for child in widget.winfo_children():
                    update_widget_colors(child)
            except:
                pass
        
        # Start recursive update from root
        update_widget_colors(self.root)
        
        # Update all specific buttons and labels with ACCENT_COLOR
        if hasattr(self, 'refresh_windows_btn'):
            self.refresh_windows_btn.config(fg=new_color, activeforeground=new_color)
        if hasattr(self, 'bait_capacity_number_label'):
            # Only update if capacity is not 0 (when 0, it should stay red)
            capacity = self.get_max_bait_capacity()
            if capacity > 0:
                self.bait_capacity_number_label.config(fg=new_color)
        if hasattr(self, 'reset_btn'):
            self.reset_btn.config(fg=new_color)
        if hasattr(self, 'select_fishes_btn'):
            self.select_fishes_btn.config(fg=new_color)
        if hasattr(self, 'drop_btn_pos_btn'):
            self.drop_btn_pos_btn.config(fg=new_color)
        if hasattr(self, 'confirm_btn_pos_btn'):
            self.confirm_btn_pos_btn.config(fg=new_color)
        if hasattr(self, 'armor_slot_btn'):
            self.armor_slot_btn.config(fg=new_color)
        if hasattr(self, 'quick_skip_help_btn'):
            self.quick_skip_help_btn.config(bg=new_color, activebackground=new_color)
        if hasattr(self, 'drop_help_btn'):
            self.drop_help_btn.config(bg=new_color, activebackground=new_color)
        if hasattr(self, 'total_games_label'):
            self.total_games_label.config(fg=new_color)
        if hasattr(self, 'active_windows_label'):
            self.active_windows_label.config(fg=new_color)
        if hasattr(self, 'bait_label'):
            self.bait_label.config(fg=new_color)
        if hasattr(self, 'start_pause_btn'):
            self.start_pause_btn.config(fg=new_color)
        if hasattr(self, 'stop_all_btn'):
            self.stop_all_btn.config(fg=new_color)
        if hasattr(self, 'donations_label'):
            self.donations_label.config(fg=new_color)
        
        # Update window bait labels
        for i in range(MAX_WINDOWS):
            if i in self.window_bait_labels:
                self.window_bait_labels[i].config(fg=new_color)
        
        # Update window games labels
        for i in range(MAX_WINDOWS):
            if i in self.window_games_labels:
                self.window_games_labels[i].config(fg=new_color)
        
        # Save to config
        self.config['accent_color'] = new_color
        if not from_rgb_wave:
            self.config['rgb_wave_active'] = False
        self.save_config()
        
        self.add_status(f"Accent color changed to {new_color}")
    
    def toggle_rgb_wave(self):
        """Toggles the RGB wave effect on/off."""
        self.rgb_wave_active = not self.rgb_wave_active
        
        if self.rgb_wave_active:
            # Show performance warning
            messagebox.showwarning(
                "RGB Wave Effect",
                "RGB Wave Effect enabled!\n\n"
                "Note: This feature may cause a slight performance drop due to continuous color updates.\n\n"
                "If you experience any issues, you can disable it by clicking the rainbow square again."
            )
            self.add_status("RGB wave effect activated")
            self.rgb_wave_hue = 0
            self.update_rgb_wave()
        else:
            self.add_status("RGB wave effect deactivated")
        
        # Save to config
        self.config['rgb_wave_active'] = self.rgb_wave_active
        self.save_config()
    
    def update_rgb_wave(self):
        """Updates the accent color with RGB wave effect."""
        if not self.rgb_wave_active:
            return
        
        # Convert HSV to RGB (hue cycles 0-360)
        import colorsys
        h = self.rgb_wave_hue / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
        
        # Convert to hex color
        color_hex = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        
        # Update accent color (from RGB wave, don't stop the wave)
        self.change_accent_color(color_hex, from_rgb_wave=True)
        
        # Increment hue for next update
        self.rgb_wave_hue = (self.rgb_wave_hue + 3) % 360
        
        # Schedule next update (60ms for smooth transition)
        if self.rgb_wave_active:
            self.root.after(60, self.update_rgb_wave)
    
    def start_position_capture(self, mode: str):
        """Starts mouse position capture mode. User clicks in game window to set position.
        mode: 'drop' or 'confirm'"""
        # If already capturing for this mode, just reactivate the stored window (and minimize others)
        if self._position_capture_mode == mode and hasattr(self, '_position_capture_window') and self._position_capture_window:
            try:
                all_windows = WindowManager.get_all_windows()
                window_dict = {name: win for name, win in all_windows}
                
                # Minimize all other selected windows first
                for i in range(MAX_WINDOWS):
                    window_name = self.window_selections[i].get()
                    if window_name and window_name != self._position_capture_window:
                        if window_name in window_dict:
                            try:
                                window_dict[window_name].minimize()
                            except Exception:
                                pass  # Ignore if minimize fails
                
                # Small delay to ensure windows are minimized
                time.sleep(0.1)
                
                # Then activate the target window
                if self._position_capture_window in window_dict:
                    target_window = window_dict[self._position_capture_window]
                    # Restore if minimized
                    if target_window.isMinimized:
                        target_window.restore()
                        time.sleep(0.1)
                    target_window.activate()
                    self.add_status(f"Click on the {mode} button in the game window...")
                    return
            except Exception:
                pass  # Fall through to normal capture startup
        
        # Check if at least one window is selected
        selected_windows = [self.window_selections[i].get() for i in range(MAX_WINDOWS) if self.window_selections[i].get()]
        if not selected_windows:
            messagebox.showerror("No Window Selected", 
                               "Please select at least one game window first!\n\n"
                               "The position will be captured relative to the selected window.")
            return
        
        self._position_capture_mode = mode
        self._position_capture_window = selected_windows[0]  # Store the target window
        
        # Update button text to show capture mode is active
        if mode == 'drop':
            self.drop_btn_pos_btn.config(text="⏳ Click in game...", bg="#f39c12")
        elif mode == 'confirm':
            self.confirm_btn_pos_btn.config(text="⏳ Click in game...", bg="#f39c12")
        else:  # armor
            self.armor_slot_btn.config(text="⏳ Click in game...", bg="#f39c12")
        
        self.add_status(f"Click on the {mode} button in the game window...")
        
        # Activate the first selected game window and minimize all others
        try:
            all_windows = WindowManager.get_all_windows()
            window_dict = {name: win for name, win in all_windows}
            
            # Minimize all other selected windows first
            for i in range(MAX_WINDOWS):
                window_name = self.window_selections[i].get()
                if window_name and window_name != self._position_capture_window:
                    if window_name in window_dict:
                        try:
                            window_dict[window_name].minimize()
                        except Exception:
                            pass  # Ignore if minimize fails
            
            # Small delay to ensure windows are minimized
            time.sleep(0.1)
            
            # Then activate the target window
            if self._position_capture_window in window_dict:
                target_window = window_dict[self._position_capture_window]
                # Restore if minimized
                if target_window.isMinimized:
                    target_window.restore()
                    time.sleep(0.1)
                target_window.activate()
        except Exception as e:
            self.add_status(f"Could not activate window: {e}")
        
        # Start mouse listener
        try:
            from pynput import mouse
            
            def on_click(x, y, button, pressed):
                if pressed and button == mouse.Button.left:
                    # Capture position relative to the first selected window
                    self.root.after(0, lambda: self._capture_position_callback(x, y, mode))
                    return False  # Stop listener
            
            self._position_capture_listener = mouse.Listener(on_click=on_click)
            self._position_capture_listener.start()
        except Exception as e:
            self.add_status(f"Error starting mouse capture: {e}")
            self._reset_position_capture_buttons()
    
    def _capture_position_callback(self, screen_x: int, screen_y: int, mode: str):
        """Callback when position is captured. Converts screen coords to window-relative."""
        try:
            # Use the stored target window from position capture start
            selected_name = getattr(self, '_position_capture_window', None)
            
            if not selected_name:
                self.add_status("No window stored for capture!")
                self._reset_position_capture_buttons()
                return
            
            # Get window rect
            all_windows = WindowManager.get_all_windows()
            window_dict = {name: win for name, win in all_windows}
            
            if selected_name not in window_dict:
                self.add_status(f"Window not found: {selected_name}")
                self._reset_position_capture_buttons()
                return
            
            selected_window = window_dict[selected_name]
            wm = WindowManager()
            wm.selected_window = selected_window
            win_left, win_top, _, _ = wm.get_window_rect()
            
            # Calculate relative position
            rel_x = screen_x - win_left
            rel_y = screen_y - win_top
            
            # Store position in config
            if mode == 'drop':
                self.config['drop_button_pos'] = (rel_x, rel_y)
                self.drop_btn_pos_label.config(text=f"({rel_x},{rel_y})", fg="#ffffff")
                self.add_status(f"Drop button position set: ({rel_x}, {rel_y})")
            elif mode == 'confirm':
                self.config['confirm_button_pos'] = (rel_x, rel_y)
                self.confirm_btn_pos_label.config(text=f"({rel_x},{rel_y})", fg="#ffffff")
                self.add_status(f"Confirm button position set: ({rel_x}, {rel_y})")
            else:  # armor
                self.config['armor_slot_pos'] = (rel_x, rel_y)
                self.armor_slot_label.config(text=f"({rel_x},{rel_y})", fg="#ffffff")
                self.add_status(f"Armor slot position set: ({rel_x}, {rel_y})")
            
            self.save_config()
            
        except Exception as e:
            self.add_status(f"Error capturing position: {e}")
        finally:
            self._reset_position_capture_buttons()
    
    def _reset_position_capture_buttons(self):
        """Resets position capture buttons to their normal state."""
        self._position_capture_mode = None
        self._position_capture_window = None  # Clear stored window
        if self._position_capture_listener:
            try:
                self._position_capture_listener.stop()
            except:
                pass
            self._position_capture_listener = None
        
        if hasattr(self, 'drop_btn_pos_btn'):
            self.drop_btn_pos_btn.config(text="Set Drop/Sell/Destroy Coords", bg="#555555")
        if hasattr(self, 'confirm_btn_pos_btn'):
            self.confirm_btn_pos_btn.config(text="Set Confirm Button Coords", bg="#555555")
        if hasattr(self, 'armor_slot_btn'):
            self.armor_slot_btn.config(text="Set Armor Slot Coords", bg="#666666")
    
    def open_fish_selection_window(self):
        """Opens the fish selection window for configuring fish/item actions."""
        # Check if window already exists and is open
        if self.fish_selection_window is not None:
            try:
                self.fish_selection_window.window.lift()
                self.fish_selection_window.window.focus_force()
                return
            except tk.TclError:
                # Window was closed, create a new one
                self.fish_selection_window = None
        
        # Create new fish selection window
        self.fish_selection_window = FishSelectionWindow(
            self.root, 
            self.config.get('fish_actions', {}),
            self.on_fish_actions_saved,
            self.config,  # Pass config to check drop button positions
            BotGUI.ACCENT_COLOR,  # Pass current accent color
            self.rgb_wave_active,  # Pass RGB wave state
            self.rgb_wave_hue  # Pass current hue
        )
    
    def on_fish_actions_saved(self, fish_actions: dict):
        """Callback when fish actions are saved from the selection window."""
        self.config['fish_actions'] = fish_actions
        self.save_config()
        self.add_status(f"Fish actions saved: {len(fish_actions)} items configured")
        
        # Update drop button states based on whether any fish is set to 'drop'
        self._update_drop_buttons_state()

    def on_window_selected(self, window_id: int):
        """Updates bait display when a window is selected."""
        selected_name = self.window_selections[window_id].get()
        
        # Check if this window is already selected in another slot (optimized check)
        if selected_name:
            selected_windows = {self.window_selections[i].get() for i in range(MAX_WINDOWS) if i != window_id}
            if selected_name in selected_windows:
                # Window already selected elsewhere, prevent duplicate
                self.window_selections[window_id].set("")
                self.add_status(f"Window '{selected_name}' is already selected in another slot")
                # Reset display
                self.window_stats[window_id]['bait'] = 0
                self.window_bait_labels[window_id].config(text="B:---")
                return
            
            # Window is selected - update bait to current capacity
            self.window_stats[window_id]['bait'] = self.bait
            self.window_bait_labels[window_id].config(text=f"B:{self.bait}")
        else:
            # Window is unselected - show --- and reset bait to 0
            self.window_stats[window_id]['bait'] = 0
            self.window_bait_labels[window_id].config(text="B:---")
        
        # Update total bait label to reflect new sum of selected windows
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label.config(text=str(total_bait))
    
    def animate_gif(self):
        """Animates the GIF frames."""
        if self.photo_images and (self.gif_label_left or self.gif_label_right):
            self.current_frame = (self.current_frame + 1) % len(self.photo_images)
            if self.gif_label_left:
                self.gif_label_left.config(image=self.photo_images[self.current_frame])
            if self.gif_label_right:
                self.gif_label_right.config(image=self.photo_images[self.current_frame])
            # Schedule next frame update (30ms for faster animation)
            self.root.after(30, self.animate_gif)
    
    def on_global_key_press(self, key):
        """Global key press handler for F5 pause/resume all bots."""
        try:
            if key == keyboard.Key.f5:
                self.root.after(0, self.toggle_pause_all_bots)
        except AttributeError:
            pass
    
    def disable_buttons_for_cooldown(self):
        """Disables all control buttons during cooldown period."""
        self.in_cooldown = True
        self.start_pause_btn.config(state=tk.DISABLED)
        self.stop_all_btn.config(state=tk.DISABLED)
        # Re-enable buttons after cooldown
        self.root.after(int(self.action_cooldown * 1000), self.end_cooldown_and_update_buttons)
    
    def end_cooldown_and_update_buttons(self):
        """Ends cooldown period and updates button states."""
        self.in_cooldown = False
        self.update_all_button_states()
    
    def start_or_pause_bots(self):
        """Combined Start/Pause/Resume handler - decides action based on current state."""
        any_running = any(bot.running for bot in self.bots.values()) if self.bots else False
        
        if not any_running:
            # No bots running - start them
            self.start_all_bots()
        else:
            # Bots are running - toggle pause
            self.toggle_pause_all_bots()
    
    def toggle_pause_all_bots(self):
        """Toggles pause state for all running bots."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        self.last_action_time = current_time
        self.disable_buttons_for_cooldown()
        
        any_running = any(bot.running for bot in self.bots.values())
        if not any_running:
            return
        
        any_paused = any(bot.paused for bot in self.bots.values() if bot.running)
        
        for bot_id, bot in self.bots.items():
            if bot.running:
                bot.paused = not any_paused
                # Update status indicator
                if bot_id in self.window_status_labels:
                    if bot.paused:
                        self.window_status_labels[bot_id].config(text="🟡", fg="#f39c12")
                    else:
                        self.window_status_labels[bot_id].config(text="🟢", fg="#00ff00")
        
        status = "PAUSED" if not any_paused else "RESUMED"
        self.add_status(f"All bots {status} (F5)")
    
    def update_stats(self, bot_id: int, hits: int, total_games: int, bait: int):
        """Updates the statistics display for a specific bot."""
        if bot_id in self.window_stats:
            self.window_stats[bot_id] = {'hits': hits, 'games': total_games, 'bait': bait}
        
        # Update individual window labels
        if bot_id in self.window_bait_labels:
            self.window_bait_labels[bot_id].config(text=f"B:{bait}")
        if bot_id in self.window_games_labels:
            self.window_games_labels[bot_id].config(text=f"G:{total_games}")
        
        # Update total statistics
        total_all_games = sum(s['games'] for s in self.window_stats.values())
        self.total_games_label.config(text=str(total_all_games))
        
        # Update total bait across selected windows only
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label.config(text=str(total_bait))
        
        # Count active windows
        active_count = len([b for b in self.bots.values() if b.running])
        self.active_windows_label.config(text=str(active_count))
    
    def reset_bait(self):
        """Resets the bait counter to max capacity for selected windows, 0 for unselected"""
        max_bait = self.get_max_bait_capacity()
        self.bait = max_bait
        
        # Reset all bots' bait counters
        for bot_id, bot in self.bots.items():
            bot.bait_counter = max_bait
            self.window_stats[bot_id]['bait'] = max_bait
            self.window_bait_labels[bot_id].config(text=f"B:{max_bait}")
        
        # Reset stats for all non-running windows
        for i in range(MAX_WINDOWS):
            if i not in self.bots:
                is_selected = self.window_selections[i].get()
                if is_selected:
                    self.window_stats[i]['bait'] = max_bait
                    self.window_bait_labels[i].config(text=f"B:{max_bait}")
                else:
                    self.window_stats[i]['bait'] = 0
                    self.window_bait_labels[i].config(text="B:---")
        
        # Update total bait label (sum of all selected windows)
        total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
        self.bait_label.config(text=str(total_bait))
        
        self.add_status(f"All bait counters reset to {max_bait}")
        self.save_config()
    
    def update_bait_from_bot(self, bot_id: int, new_bait: int):
        """Updates GUI bait counter when bot adjusts bait tier."""
        if bot_id in self.window_stats:
            self.window_stats[bot_id]['bait'] = new_bait
        if bot_id in self.window_bait_labels:
            self.window_bait_labels[bot_id].config(text=f"B:{new_bait}")
        self.save_config()
    
    def start_all_bots(self):
        """Starts bots for all selected windows."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        self.last_action_time = current_time
        self.disable_buttons_for_cooldown()
        
        # Check if bait keys are selected FIRST (before checking bait amounts)
        selected_bait_keys = self.get_selected_bait_keys()
        if not selected_bait_keys:
            messagebox.showerror("No Bait Keys Selected", 
                               "Please select at least one bait key!\n\n"
                               "Available bait keys: 1, 2, 3, 4, F1, F2, F3, F4")
            return
        
        # Check if any selected window has 0 bait - force user to reset bait before starting
        windows_with_no_bait = [i + 1 for i in range(MAX_WINDOWS) 
                                if self.window_selections[i].get() and self.window_stats[i]['bait'] <= 0]
        if windows_with_no_bait:
            window_list = ", ".join(f"W{w}" for w in windows_with_no_bait)
            messagebox.showerror("No Bait", 
                               f"Bait counter is at 0 for: {window_list}\n\n"
                               "Please click 'Reset Client Bait' button to refill your bait "
                               "before starting the bot.")
            return
        
        # Check if drop positions are configured when any item is set to 'drop'
        if self.config.get('auto_fish_handling', False):
            fish_actions = self.config.get('fish_actions', {})
            items_to_drop = [name for name, action in fish_actions.items() if action == 'drop']
            
            if items_to_drop:
                drop_pos = self.config.get('drop_button_pos')
                confirm_pos = self.config.get('confirm_button_pos')
                
                missing_positions = []
                if not drop_pos:
                    missing_positions.append("Drop Button")
                if not confirm_pos:
                    missing_positions.append("Confirm Button")
                
                if missing_positions:
                    messagebox.showerror("Configure button positions!", 
                                       "The fishbot needs to know where to click in order to to drop/sell/destroy items.\n\n"
                                       f"The following buttons is still not configured:\n\n"
                                       f"• {chr(10).join(missing_positions)}\n\n"
                                       "Please configure the drop/sell/destroy and the confirm button positions in the\n"
                                       "'Automatic Fish Handling' section before starting the bot.\n\n"
                                       "STEPS TO CONFIGURE:\n"
                                       "1. Drop an item to the floor and don't press anything (only to open the drop/destroy/sell window)\n"
                                       "2. Click 'Set Drop/Sell/Destroy Button Coords' and click on the drop/sell/destroy button in the game\n"
                                       "3. Click 'Set Confirm Button Coords' and click on the confirm button to finalize dropping the item in game\n"
                                       "4. Done! You can now start the bot safely.")
                    return
        
        # Check if armor slot position is configured when quick skip is enabled with armor mode
        if self.config.get('quick_skip', False) and self.config.get('quick_skip_mode', 'horse') == 'armor':
            armor_pos = self.config.get('armor_slot_pos')
            if not armor_pos:
                messagebox.showerror("Quick Skip - Armor Mode", 
                               "This mode right-clicks on your armor slot to quickly skip the fishing animation.\n\n"
                               "HOW IT WORKS:\n"
                               "• After catching a fish, the bot right-clicks on your armor slot\n"
                               "• This unequips/re-equips your armor\n"
                               "• The equip animation skips the fishing animation\n\n"
                               "STEPS TO CONFIGURE:\n"
                               "1. Make sure your character has armor equipped\n"
                               "2. Click 'Set Armor Slot Coords' button\n"
                               "3. Click on the armor slot in your game inventory\n"
                               "4. Done! You can now start the bot with armor quick skip enabled.")
                return
        
        # Reset sound alert flag for new session
        self._sound_alert_played = False
        
        # Get config
        self.config['human_like_clicking'] = self.human_like_var.get()
        self.config['quick_skip'] = self.quick_skip_var.get()
        self.config['sound_alert_on_finish'] = self.sound_alert_var.get()
        self.config['classic_fishing'] = self.classic_fishing_var.get()
        # Update delay from entry field
        try:
            self.config['classic_fishing_delay'] = float(self.classic_delay_var.get())
        except ValueError:
            self.config['classic_fishing_delay'] = 3.0
        self.save_config()
        
        # Get all available windows
        all_windows = WindowManager.get_all_windows()
        window_dict = {name: win for name, win in all_windows}
        
        # Start a bot for each selected window
        started_count = 0
        for bot_id in range(MAX_WINDOWS):
            selected_name = self.window_selections[bot_id].get()
            if not selected_name:
                continue
            
            if selected_name not in window_dict:
                self.add_status(f"[W{bot_id+1}] Window not found: {selected_name}")
                continue
            
            # Skip if bot already running for this window
            if bot_id in self.bots and self.bots[bot_id].running:
                continue
            
            selected_window = window_dict[selected_name]
            
            # Create window manager for this bot
            wm = WindowManager()
            wm.selected_window = selected_window
            self.window_managers[bot_id] = wm
            
            # Create and configure bot
            # Use current bait for this window (preserves remaining bait if bot was stopped)
            current_bait = self.window_stats[bot_id]['bait'] if self.window_stats[bot_id]['bait'] > 0 else self.bait
            bot = FishingBot(
                None, 
                self.config.copy(), 
                wm, 
                bait_counter=current_bait, 
                bait_keys=selected_bait_keys.copy(),
                bot_id=bot_id
            )
            bot.on_status_update = self.add_status
            bot.on_stats_update = self.update_stats
            bot.on_bait_update = self.update_bait_from_bot
            bot.on_bot_stop = self.on_bot_stopped
            
            bot.running = True
            self.bots[bot_id] = bot
            
            # Initialize stats
            self.window_stats[bot_id] = {'hits': 0, 'games': 0, 'bait': self.bait}
            
            # Create ignored positions debug window (only if DEBUG_MODE_EN is true)
            if DEBUG_MODE_EN:
                self.ignored_positions_windows[bot_id] = IgnoredPositionsWindow(self.root, bot)
                self.fish_detector_debug_windows[bot_id] = FishDetectorDebugWindow(self.root, bot)
            
            # Start bot thread
            thread = threading.Thread(target=bot.start, daemon=True)
            thread.start()
            self.bot_threads[bot_id] = thread
            
            # Update status indicator
            self.window_status_labels[bot_id].config(text="🟢", fg="#00ff00")
            self.window_combos[bot_id].config(state="disabled")
            
            started_count += 1
            self.add_status(f"[W{bot_id+1}] Bot started for: {selected_name}")
        
        if started_count == 0:
            messagebox.showerror("Error", "Please select at least one window!")
            return
        
        # Disable configuration widgets while bots are running
        self.set_config_widgets_state('disabled')
        
        self.add_status(f"Started {started_count} bot(s)")
    
    def stop_all_bots(self):
        """Stops all running bots."""
        # Check cooldown
        current_time = time.time()
        if current_time - self.last_action_time < self.action_cooldown:
            return
        self.last_action_time = current_time
        self.disable_buttons_for_cooldown()
        
        for bot_id, bot in list(self.bots.items()):
            bot.running = False
            bot.stop()
            self.window_status_labels[bot_id].config(text="⚪", fg="#888888")
            self.window_combos[bot_id].config(state="readonly")
        
        self.bots.clear()
        self.bot_threads.clear()
        
        # Re-enable configuration widgets when all bots stop
        self.set_config_widgets_state('normal')
        
        self.add_status("All bots stopped")
    
    def update_all_button_states(self):
        """Updates all control buttons based on bot states."""
        # Don't update buttons during cooldown period
        if self.in_cooldown:
            return
        
        any_running = any(bot.running for bot in self.bots.values()) if self.bots else False
        any_paused = any(bot.paused for bot in self.bots.values() if bot.running) if self.bots else False
        
        if any_running:
            self.start_pause_btn.config(state=tk.NORMAL)
            self.stop_all_btn.config(state=tk.NORMAL)
            
            if any_paused:
                # Show Resume button
                self.start_pause_btn.config(text="▶ Resume All (F5)", bg="#888888", activebackground="#999999")
            else:
                # Show Pause button
                self.start_pause_btn.config(text="⏸ Pause All (F5)", bg="#888888", activebackground="#999999")
        else:
            # No bots running - show Start button
            self.start_pause_btn.config(state=tk.NORMAL, text="▶ Start All", bg="#888888", activebackground="#999999")
            self.stop_all_btn.config(state=tk.DISABLED)
        
        # Update active windows count
        active_count = len([b for b in self.bots.values() if b.running])
        self.active_windows_label.config(text=str(active_count))
    
    def set_config_widgets_state(self, state: str):
        """Enables or disables all configuration widgets.
        state: 'normal' to enable, 'disabled' to disable."""
        # Classic fishing checkbox and delay entry
        self.classic_fishing_check.config(state=state)
        self.classic_delay_entry.config(state=state)
        
        # Human-like clicking checkbox
        self.human_like_check.config(state=state)
        
        # Quick skip checkbox and mode checkboxes
        self.quick_skip_check.config(state=state)
        # Mode checkboxes should only be enabled if quick skip is enabled and we're enabling widgets
        if state == 'normal' and self.quick_skip_var.get():
            self.quick_skip_mode_horse_check.config(state=state)
            self.quick_skip_mode_armor_check.config(state=state)
        else:
            self.quick_skip_mode_horse_check.config(state='disabled')
            self.quick_skip_mode_armor_check.config(state='disabled')
        
        # Sound alert checkbox
        self.sound_alert_check.config(state=state)
        
        # Bait key checkboxes
        for cb in self.bait_key_checkboxes.values():
            cb.config(state=state)
        
        # Reset bait button
        self.reset_btn.config(state=state)
        
        # Automatic fish handling checkbox and select fishes button
        self.auto_fish_check.config(state=state)
        # Only enable select fishes button if auto fish handling is enabled and we're enabling widgets
        if state == 'normal' and self.auto_fish_var.get():
            self.select_fishes_btn.config(state=tk.NORMAL)
        else:
            self.select_fishes_btn.config(state=tk.DISABLED)
        
        # Drop/Confirm button coordinate capture buttons
        # Only enable when widgets are being enabled (bots stopped) and auto fish handling is on
        if state == 'normal' and self.auto_fish_var.get():
            if hasattr(self, 'drop_btn_pos_btn'):
                self.drop_btn_pos_btn.config(state=tk.NORMAL)
            if hasattr(self, 'confirm_btn_pos_btn'):
                self.confirm_btn_pos_btn.config(state=tk.NORMAL)
        else:
            if hasattr(self, 'drop_btn_pos_btn'):
                self.drop_btn_pos_btn.config(state=tk.DISABLED)
            if hasattr(self, 'confirm_btn_pos_btn'):
                self.confirm_btn_pos_btn.config(state=tk.DISABLED)
        
        # Refresh Windows button
        if hasattr(self, 'refresh_windows_btn'):
            self.refresh_windows_btn.config(state=state)
    
    def on_bot_stopped(self, bot_id: int):
        """Updates UI when a bot stops running."""
        if bot_id in self.window_status_labels:
            self.window_status_labels[bot_id].config(text="🔴", fg="#e74c3c")
        if bot_id in self.window_combos:
            self.window_combos[bot_id].config(state="readonly")
        
        # Destroy ignored positions window
        if bot_id in self.ignored_positions_windows:
            self.ignored_positions_windows[bot_id].destroy()
            del self.ignored_positions_windows[bot_id]
        
        # Destroy fish detector debug window
        if bot_id in self.fish_detector_debug_windows:
            self.fish_detector_debug_windows[bot_id].destroy()
            del self.fish_detector_debug_windows[bot_id]
        
        # Remove from active bots
        if bot_id in self.bots:
            del self.bots[bot_id]
        if bot_id in self.bot_threads:
            del self.bot_threads[bot_id]
        
        # Check if all bots stopped
        if not self.bots:
            # Re-enable configuration widgets when all bots stop
            self.set_config_widgets_state('normal')
            
            # Play sound alert if all selected windows are out of bait (only once per session)
            if self.config.get('sound_alert_on_finish', True) and not self._sound_alert_played:
                total_bait = sum(self.window_stats[i]['bait'] for i in range(MAX_WINDOWS) if self.window_selections[i].get())
                if total_bait <= 0:
                    self._sound_alert_played = True
                    from utils import play_rickroll_beep
                    play_rickroll_beep()
        
        self.update_all_button_states()
    
    def run(self):
        """Starts the GUI application."""
        # FAILSAFE already disabled at module level for multi-window support
        pyautogui.PAUSE = 0.01
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()
    
    def on_close(self):
        """
        Handles the window close event.
        Stops all bots and saves configuration before closing.
        """
        # Stop all running bots
        for bot in self.bots.values():
            bot.running = False
        
        # Stop global keyboard listener
        if self.global_key_listener:
            self.global_key_listener.stop()
        
        # Destroy status log window
        if hasattr(self, 'status_log_window') and self.status_log_window:
            self.status_log_window.destroy()
        
        # Destroy all ignored positions windows
        for window in self.ignored_positions_windows.values():
            try:
                window.destroy()
            except:
                pass
        
        # Destroy all fish detector debug windows
        for window in self.fish_detector_debug_windows.values():
            try:
                window.destroy()
            except:
                pass
        
        self.save_config()
        self.root.destroy()
    
    def copy_btc_address(self):
        """Copies the BTC address to clipboard."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.btc_address)
            self.add_status(f"BTC address copied: {self.btc_address}")
        except Exception as e:
            self.add_status(f"Error copying address: {e}")
