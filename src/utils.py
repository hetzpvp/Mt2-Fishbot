"""
Utility functions and constants for the Fishing Bot
"""

import os
import sys
import threading
import time
from contextlib import contextmanager

try:
    import winsound
except ImportError:  # non-Windows (tests / tooling)
    winsound = None


class PriorityInputLock:
    """Mutex for the single shared mouse/keyboard, with two priority classes.

    All bots share one cursor and one foreground window, so every input
    sequence must be serialized. A plain ``threading.Lock`` hands the lock to
    an arbitrary waiter, which lets a slow inventory/cast sequence jump ahead
    of a bot whose fish is inside the circle *right now*.

    Waiters are served strictly by (priority, arrival order):

    - ``with input_lock:``            normal priority (casts, inventory, keys)
    - ``with input_lock.urgent():``   time-critical (minigame fish click)

    Arrival order inside a class makes the lock fair, so no bot can starve
    another by re-acquiring in a tight loop.

    It is not re-entrant: a thread that asks for it while holding it would
    wait for itself — and every other bot with it, silently. That raises
    instead. Holds longer than ``LONG_HOLD`` seconds (other windows' fish
    clicks wait that long) go to the diagnostic log.
    """

    URGENT = 0
    NORMAL = 1
    LONG_HOLD = 0.5

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._held = False
        self._owner = None          # thread ident of the holder
        self._since = 0.0           # perf_counter() when it was taken
        self._seq = 0
        self._waiters = []  # sorted list of (priority, seq)

    def _take(self) -> None:
        self._held = True
        self._owner = threading.get_ident()
        self._since = time.perf_counter()

    def acquire(self, priority: int = NORMAL) -> bool:
        with self._cond:
            if self._held and self._owner == threading.get_ident():
                raise RuntimeError("input_lock is not re-entrant: this thread already holds it")
            if not self._held and not self._waiters:
                self._take()
                return True
            self._seq += 1
            ticket = (priority, self._seq)
            self._waiters.append(ticket)
            self._waiters.sort()
            try:
                while self._held or self._waiters[0] != ticket:
                    self._cond.wait()
            except BaseException:
                self._waiters.remove(ticket)
                self._cond.notify_all()
                raise
            self._waiters.pop(0)
            self._take()
            return True

    def release(self) -> None:
        with self._cond:
            held_for = time.perf_counter() - self._since
            self._held = False
            self._owner = None
            self._cond.notify_all()
        if held_for > self.LONG_HOLD:
            try:
                import diag
                if diag.ENABLED:
                    diag.log("LOCK", f"input_lock held for {held_for:.2f}s (> {self.LONG_HOLD}s): other "
                                     f"windows waited that long", depth=1)
            except Exception:
                pass

    def held_by_me(self) -> bool:
        """True when the calling thread holds the lock."""
        return self._held and self._owner == threading.get_ident()

    def locked(self) -> bool:
        return self._held

    def contended(self) -> bool:
        """True when at least one other thread is waiting for the lock."""
        return bool(self._waiters)

    def urgent_waiting(self) -> bool:
        """True when a time-critical (urgent) request is waiting."""
        with self._cond:
            return bool(self._waiters) and self._waiters[0][0] == self.URGENT

    def __enter__(self):
        self.acquire(self.NORMAL)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    @contextmanager
    def urgent(self):
        self.acquire(self.URGENT)
        try:
            yield self
        finally:
            self.release()


# Thread synchronization for mouse/keyboard - prevents race conditions
input_lock = PriorityInputLock()


# ---------------- Keyboard vs. focus switches ----------------
# Keys go to whichever window has the keyboard focus when the desktop
# delivers them, not when they were sent. With several clients that is a
# race (seen on KDE Wayland + XWayland + Wine, 2026-11-19): a Ctrl+G whose
# release went out 0-1 ms before another bot switched the focus, and a bait
# key sent 56 ms after the switch back, never reached their window. The
# cast was lost and the bot waited out the bite timeout. So:
#   - no focus switch until the last key event has had KEY_FLUSH to arrive;
#   - no key until the last switch has had KEY_AFTER_SWITCH to settle (the
#     game handles its activation on its own frame, which is slow for a
#     client that was in the background).
# Mouse clicks go to the window under the cursor, but the game still loses
# one when it loses the focus right after it: in diagnostics 20261120_195925
# (two clients, Wine) 37 of 63 minigame clicks were not counted when the
# other bot switched the focus 20-30 ms after the click, and none of 16 when
# the switch came 30 ms or later. So a focus switch also waits until the
# last click is CLICK_FLUSH old.
KEY_FLUSH = 0.06
KEY_AFTER_SWITCH = 0.15
CLICK_FLUSH = 0.05

_key_focus = {"key": 0.0, "switch": 0.0, "click": 0.0}


def note_key_event() -> None:
    """A key event was just sent (call after each press / release)."""
    _key_focus["key"] = time.perf_counter()


def note_click_event() -> None:
    """A mouse button event was just sent (call after each button release)."""
    _key_focus["click"] = time.perf_counter()


def note_focus_switch() -> None:
    """The foreground window just changed."""
    _key_focus["switch"] = time.perf_counter()


def _wait_since(stamp: str, gap: float) -> float:
    left = _key_focus[stamp] + gap - time.perf_counter()
    if left > 0:
        time.sleep(left)
        return left
    return 0.0


def wait_keys_delivered() -> float:
    """Before a focus switch: waits until the last key event is KEY_FLUSH old
    and the last click CLICK_FLUSH old. Returns the seconds waited."""
    waited = _wait_since("key", KEY_FLUSH)
    return waited + _wait_since("click", CLICK_FLUSH)


def wait_focus_settled_for_keys() -> float:
    """Before a key: waits until the last focus switch is KEY_AFTER_SWITCH
    old. Returns the seconds waited."""
    return _wait_since("switch", KEY_AFTER_SWITCH)

# Max simultaneous game windows
MAX_WINDOWS = 8

# Support Discord server invite (shown as a clickable button in the GUI header)
DISCORD_INVITE_URL = "https://discord.gg/vZaHCRFqW7"

# Debug mode - enable/disable IgnoredPositionsWindow
DEBUG_MODE_EN = False

# Debug prints - enable/disable verbose debug print statements
DEBUG_PRINTS = False

FISH_ACTIONS = ("keep", "drop", "open")


# Fish the game takes as bait: a right-click puts one on the rod (it does not
# open). Their "use" action fishes with them instead of the bait key.
BAIT_FISH = ("Shrimp_living.jpg", "Vai_living.jpg")


def is_bait_fish(filename) -> bool:
    return filename in BAIT_FISH


def normalize_fish_action(action, filename=None) -> str:
    """A configured fish / item action as the bot acts on it. Only "drop",
    "open" and, for a bait fish, "use" do anything; every other value
    (missing, empty, a typo in a hand-edited config) keeps the item, so a
    broken entry never drops one. With ``filename`` given, a bait fish set
    to "open" (a right-click, which puts it on the rod) means "use", and
    any other item set to "use" is kept."""
    if action == "use" or (action == "open" and is_bait_fish(filename)):
        if filename is None or is_bait_fish(filename):
            return "use"
        return "keep"
    return action if action in ("drop", "open") else "keep"


def dead_fish_action(living_action) -> str:
    """What the dead kind of a fish does, given its living fish's action. A
    dead fish cannot be opened or used as bait, so it follows its living
    fish only for drop; every other choice keeps it."""
    return "drop" if living_action == "drop" else "keep"


def with_dead_fish_actions(actions) -> dict:
    """``actions`` with every ``<Fish>_dead.png`` set from its
    ``<Fish>_living.jpg`` (see dead_fish_action). The dead kinds have no
    choice of their own: whoever keeps a fish keeps it dead too. Dead
    entries without a living one stay as they are."""
    out = dict(actions or {})
    for name, action in list(out.items()):
        if isinstance(name, str) and name.endswith("_living.jpg"):
            out[name[:-len("_living.jpg")] + "_dead.png"] = dead_fish_action(
                normalize_fish_action(action, name))
    return out


# What a fish / item template's name ends with, and how it is shown.
# A fish comes in three states, each its own item with its own icon.
_ITEM_SUFFIXES = (("_living", ""), ("_item", ""), ("_dead", " (dead)"),
                  ("_grilled", " (grilled)"), ("_unsure", " (dead or alive?)"))


def item_display_name(filename: str) -> str:
    """'Zander_dead.png' -> 'Zander (dead)', 'Carp_living.jpg' -> 'Carp'."""
    stem = os.path.splitext(filename)[0]
    for suffix, label in _ITEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[:-len(suffix)] + label
    return stem


def get_project_root() -> str:
    """Return the repository root in development, or the bundle root in PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(__file__))


def _asset_candidates(base_dir: str, filename: str) -> list:
    normalized = filename.replace("\\", os.sep).replace("/", os.sep)
    asset_root = os.path.join(base_dir, "assets")

    if normalized in ("asset_root", "assets_root"):
        return [asset_root]
    if normalized == "assets":
        # Backwards-compatible alias used by the fishing template loaders.
        return [os.path.join(asset_root, "fishing")]
    if normalized.startswith("assets" + os.sep):
        return [os.path.join(base_dir, normalized)]

    return [
        os.path.join(base_dir, normalized),
        os.path.join(asset_root, normalized),
        os.path.join(asset_root, "fishing", normalized),
        os.path.join(asset_root, "ui", normalized),
        os.path.join(asset_root, "jigsaw", normalized),
    ]


def get_resource_path(filename: str) -> str:
    """Get the path to a bundled resource (works both in dev and in PyInstaller exe)
    
    Assets like images (.gif, .ico, .jpg, .png) are looked up in the assets folder.
    """
    base_dir = get_project_root()
    candidates = _asset_candidates(base_dir, filename)
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


def set_window_icon(window, icon_path: str):
    """Sets window icon using multiple methods for maximum compatibility.
    
    This function ensures the icon appears in both the window title bar and Windows taskbar.
    Uses both iconbitmap() and iconphoto() for better PyInstaller compatibility.
    
    Args:
        window: Tkinter window (Tk or Toplevel)
        icon_path: Path to .ico file
    """
    if not os.path.exists(icon_path):
        return
    
    try:
        # Method 1: Use iconbitmap for title bar (Windows-specific)
        window.iconbitmap(icon_path)
        window.iconbitmap(default=icon_path)
    except Exception:
        pass
    
    try:
        # Method 2: Use iconphoto for taskbar (cross-platform, more reliable with PyInstaller)
        # Convert .ico to PhotoImage using PIL
        from PIL import Image, ImageTk
        
        # Load .ico and convert to PhotoImage
        icon_image = Image.open(icon_path)
        # Use the largest size available in the .ico
        if hasattr(icon_image, 'size'):
            photo = ImageTk.PhotoImage(icon_image)
            window.iconphoto(True, photo)
            # Keep a reference to prevent garbage collection
            window._icon_photo = photo
    except Exception:
        pass


def load_window_icon(window) -> None:
    """Applies monkey.ico to a Tk Toplevel window. Silently skips if the file is missing."""
    set_window_icon(window, get_resource_path("monkey.ico"))


def _play_wav_file(path: str) -> bool:
    """Linux: plays a WAV file with the first player found (PipeWire,
    PulseAudio, ALSA) and waits for it. False when none could play it."""
    import shutil
    import subprocess
    for player in (["pw-play"], ["paplay"], ["aplay", "-q"]):
        exe = shutil.which(player[0])
        if not exe:
            continue
        try:
            if subprocess.run([exe] + player[1:] + [path], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30).returncode == 0:
                return True
        except Exception:
            continue
    return False


# The no-bait alert
_RICKROLL_MELODY = [
    (554, 600),   # C5s - strong opening
    (622, 1000),  # E5f - longer note
    (622, 600),   # E5f
    (698, 600),   # F5
    (831, 100),   # A5f - quick notes
    (740, 100),   # F5s
    (698, 100),   # F5
    (622, 100),   # E5f
    (554, 600),   # C5s
    (622, 800),   # E5f - held note
    (415, 400),   # A4f - step down
    (415, 200),   # A4f
]
# Pause: falling two-note chime. Resume: rising. Short and unlike the alert.
_PAUSE_MELODY = [(880, 90), (587, 160)]
_RESUME_MELODY = [(587, 90), (880, 160)]


def _melody_wav(melody, volume: float) -> bytes:
    """A mono 16-bit WAV of ``melody`` [(Hz, ms), ...]: sine notes with
    smooth attack and release, 10 ms apart."""
    import io
    import wave
    import numpy as np

    sample_rate = 44100
    audio_data = []
    for frequency, duration in melody:
        num_samples = int(sample_rate * duration / 1000)
        t = np.linspace(0, duration / 1000, num_samples, False)
        wave_data = np.sin(2.0 * np.pi * frequency * t)

        # ADSR-like envelope: attack and release ramps (exponential for musicality)
        attack_ms = min(20, duration * 0.12)      # 20ms or 12% of note
        release_ms = min(40, duration * 0.25)     # 40ms or 25% of note
        attack_samples = max(int(sample_rate * attack_ms / 1000), 1)
        release_samples = max(int(sample_rate * release_ms / 1000), 1)
        envelope = np.ones(num_samples)
        if attack_samples < num_samples:
            envelope[:attack_samples] = (np.linspace(0, 1, attack_samples) ** 1.5)
        if release_samples < num_samples:
            envelope[-release_samples:] = (np.linspace(1, 0, release_samples) ** 1.5)

        audio_data.extend(wave_data * envelope * volume)
        audio_data.extend(np.zeros(int(sample_rate * 0.01)))   # gap between notes

    audio_array = np.clip(np.array(audio_data, dtype=np.float32), -1.0, 1.0)
    audio_int16 = (audio_array * 32767).astype(np.int16)
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)       # Mono
        wav_file.setsampwidth(2)       # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())
    return wav_buffer.getvalue()


def _play_melody(melody, volume: float) -> None:
    """Plays ``melody`` and waits for it (call it from a thread). Falls back
    to winsound beeps on Windows when the WAV cannot be made. Silent when
    FISHBOT_MUTE=1 (the test suite sets it)."""
    if os.environ.get("FISHBOT_MUTE") == "1":
        return
    try:
        import tempfile
        import time
        data = _melody_wav(melody, volume)
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            if winsound is not None:
                winsound.PlaySound(tmp_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            else:
                _play_wav_file(tmp_path)
        finally:
            time.sleep(0.1)  # Small delay before cleanup
            os.remove(tmp_path)
    except Exception:
        if winsound is None:
            return
        import time
        for frequency, duration in melody:
            winsound.Beep(frequency, duration)
            time.sleep(0.01)  # Small gap between notes


def play_rickroll_beep():
    """Plays a Rick Roll-themed beep sequence (the no-bait alert)."""
    _play_melody(_RICKROLL_MELODY, 0.28)


def play_pause_beep(paused: bool) -> None:
    """A short chime when the bots are paused (falling) or resumed (rising),
    so F5 is confirmed while the game window is in front."""
    _play_melody(_PAUSE_MELODY if paused else _RESUME_MELODY, 0.22)
