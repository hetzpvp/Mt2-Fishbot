"""
Fish Detector for the Fishing Bot.

Detection is driven by a ``DetectionProfile`` (HSV ranges + geometry). The
default profile reproduces the original hard-coded values for the official
client, so behavior there is unchanged.

Other Metin2 clients re-skin the minigame (different panel color, different
fish color, sometimes a different size). Two mechanisms cope with that:

1. Relative thresholds. "Is the minigame open" is judged against the pixel
   count measured when the panel was calibrated, not against a fixed 10000 px,
   so smaller panels work.

2. Self-calibration (``WindowLearner`` / ``FishLearner``). When the active
   profile finds nothing, the panel is located by *structure* instead of
   color — a large, dense rectangle that appeared after the cast — and its
   dominant color is learned. The fish is then learned as "the thing that
   moves on that background". The result is an ordinary profile, so the hot
   path stays two ``inRange`` calls.
"""

import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Deque, List, Optional, Tuple

import cv2
import numpy as np

import diag

Bounds = Tuple[int, int, int, int]
HSV = Tuple[int, int, int]


@dataclass
class DetectionProfile:
    """Everything client-specific about the minigame's look."""
    name: str = "official"
    window_lower: HSV = (98, 170, 189)    # minigame panel background (cyan)
    window_upper: HSV = (106, 255, 250)
    fish_lower: Optional[HSV] = (97, 130, 108)   # fish (blue-ish); None = not learned yet
    fish_upper: Optional[HSV] = (110, 146, 133)
    circle_radius: int = 67               # click-zone radius in px
    # Click-zone center relative to the panel's top-left corner.
    # None = the middle of the panel (official layout).
    circle_center: Optional[Tuple[int, int]] = None
    window_min_pixels: int = 10000        # panel pixels needed to count as "open"
    fish_min_pixels: int = 24             # below this the fish mask is noise
    learned: bool = False                 # produced by self-calibration

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("window_lower", "window_upper", "fish_lower", "fish_upper"):
            if d[k] is not None:
                d[k] = [int(v) for v in d[k]]
        if d["circle_center"] is not None:
            d["circle_center"] = [int(v) for v in d["circle_center"]]
        return d

    @classmethod
    def from_dict(cls, data) -> Optional["DetectionProfile"]:
        """Builds a profile from config data. Returns None if it is unusable."""
        if not isinstance(data, dict):
            return None
        try:
            base = cls()

            def hsv(key, default):
                v = data.get(key, default)
                if v is None:
                    return None
                v = tuple(int(x) for x in v)
                if len(v) != 3:
                    raise ValueError(key)
                return (min(179, max(0, v[0])), min(255, max(0, v[1])), min(255, max(0, v[2])))

            center = data.get("circle_center")
            if center is not None:
                center = tuple(int(x) for x in center)
                if len(center) != 2 or min(center) < 0:
                    raise ValueError("circle_center")

            profile = cls(
                name=str(data.get("name", "custom")),
                window_lower=hsv("window_lower", base.window_lower),
                window_upper=hsv("window_upper", base.window_upper),
                fish_lower=hsv("fish_lower", base.fish_lower),
                fish_upper=hsv("fish_upper", base.fish_upper),
                circle_radius=max(5, int(data.get("circle_radius", base.circle_radius))),
                circle_center=center,
                window_min_pixels=max(1, int(data.get("window_min_pixels", base.window_min_pixels))),
                fish_min_pixels=max(1, int(data.get("fish_min_pixels", base.fish_min_pixels))),
                learned=bool(data.get("learned", False)),
            )
            if profile.window_lower is None or profile.window_upper is None:
                return None
            if (profile.fish_lower is None) != (profile.fish_upper is None):
                return None
            return profile
        except (TypeError, ValueError):
            return None


_CLOSE_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))


def _hsv_mask(hsv: np.ndarray, lower, upper) -> np.ndarray:
    """inRange that understands hue wrap-around (lower H > upper H, e.g. reds)."""
    if lower[0] <= upper[0]:
        return cv2.inRange(hsv, lower, upper)
    lo2 = np.array([0, lower[1], lower[2]], dtype=np.uint8)
    hi1 = np.array([179, upper[1], upper[2]], dtype=np.uint8)
    return cv2.bitwise_or(cv2.inRange(hsv, lower, hi1), cv2.inRange(hsv, lo2, upper))


def _robust_hsv_range(pixels: np.ndarray, lo_pct: float, hi_pct: float,
                      h_margin: int, sv_margin: int) -> Tuple[HSV, HSV]:
    """Percentile-based HSV range for a cluster of pixels (N x 3).

    Hue is circular, so percentiles are taken after rotating the cluster's
    median hue to the middle of the range. Near-gray clusters get the full hue
    range because hue is meaningless at low saturation."""
    h = pixels[:, 0].astype(np.int16)
    s = pixels[:, 1]
    v = pixels[:, 2]

    s_lo = int(max(0, np.percentile(s, lo_pct) - sv_margin))
    s_hi = int(min(255, np.percentile(s, hi_pct) + sv_margin))
    v_lo = int(max(0, np.percentile(v, lo_pct) - sv_margin))
    v_hi = int(min(255, np.percentile(v, hi_pct) + sv_margin))

    if float(np.median(s)) < 35:
        return (0, 0, v_lo), (179, s_hi, v_hi)

    # Circular median via the densest hue bin
    peak = int(np.bincount(h, minlength=180).argmax())
    rotated = (h - peak + 90) % 180
    r_lo = float(np.percentile(rotated, lo_pct)) - h_margin
    r_hi = float(np.percentile(rotated, hi_pct)) + h_margin
    if r_hi - r_lo >= 178:
        return (0, s_lo, v_lo), (179, s_hi, v_hi)
    h_lo = (int(round(r_lo)) + peak - 90) % 180
    h_hi = (int(round(r_hi)) + peak - 90) % 180
    return (h_lo, s_lo, v_lo), (h_hi, s_hi, v_hi)


def _dominant_cluster(pixels: np.ndarray) -> np.ndarray:
    """The pixels (N x 3 HSV) that belong to the most common color.

    A coarse 3-D histogram finds the dominant color bin; everything in its
    neighbourhood is the cluster. Stray pixels of other colors — however
    many different ones — are left out."""
    hist = cv2.calcHist([pixels.reshape(-1, 1, 3)], [0, 1, 2], None, [45, 8, 8],
                        [0, 180, 0, 256, 0, 256])
    hi, si, vi = np.unravel_index(int(hist.argmax()), hist.shape)
    peak = (hi * 4 + 2, si * 32 + 16, vi * 32 + 16)
    sat = pixels[:, 1].astype(np.int16)
    near = (np.abs(sat - peak[1]) <= 56) & (np.abs(pixels[:, 2].astype(np.int16) - peak[2]) <= 56)
    if peak[1] >= 48:
        # Hue only means something for a color that is not gray
        dh = np.abs(pixels[:, 0].astype(np.int16) - peak[0])
        near &= np.minimum(dh, 180 - dh) <= 10
    return pixels[near]


class BackgroundModel:
    """What the panel looks like without the fish.

    Built from frames spread over time: per pixel, the band of values it
    normally takes (a low and a high percentile, so the fish — which covers
    any one pixel only briefly — does not count). A static pixel has a band
    of one value; a pixel of an animated background (moving water, shimmer)
    has a wider one. Anything outside its band is "changed".

    Lets a learned profile tell the fish from everything that is always
    there — circle outline, labels, a textured, two-tone or animated
    background — whatever its color. A first model is built from a few quick
    samples so it is usable within a second, then refined once with samples
    spread over a longer time (which removes the "ghost" a slow fish can
    leave in the first model and covers a full animation cycle)."""

    FAST_INTERVAL = 0.05   # seconds between samples for the first model
    SLOW_INTERVAL = 0.15   # ... and for the refinement
    FIRST = 9              # samples in the first model
    FULL = 25              # samples in the final model
    DIFF = 22              # distance outside the band that counts as "changed"
    BAND = 0.15            # share of samples ignored at each end of the band

    def __init__(self):
        self._frames: List[np.ndarray] = []
        self._last = float("-inf")
        self._built_from = 0
        self.image: Optional[np.ndarray] = None   # median: the typical picture
        self._lower: Optional[np.ndarray] = None
        self._upper: Optional[np.ndarray] = None

    @property
    def count(self) -> int:
        return max(len(self._frames), self._built_from)

    @property
    def complete(self) -> bool:
        return self._built_from >= self.FULL

    def add(self, frame: np.ndarray, now: float) -> None:
        """Offers a panel frame as a sample (taken only when one is due)."""
        frames = self._frames
        if self.complete:
            return
        if frames and frames[0].shape != frame.shape:
            self.__init__()
            frames = self._frames
        if now - self._last < (self.FAST_INTERVAL if len(frames) < self.FIRST else self.SLOW_INTERVAL):
            return
        self._last = now
        frames.append(frame.copy())
        if len(frames) == self.FIRST or len(frames) >= self.FULL:
            self.build()

    def build(self) -> bool:
        """(Re)computes the model from the samples collected so far."""
        if self.complete:
            return True   # final: the samples are gone, the model stays
        n = len(self._frames)
        if n < 3:
            return False
        lo = int(n * self.BAND)
        hi = n - 1 - lo
        mid = n // 2
        # One selection pass puts the three order statistics in place.
        part = np.partition(np.stack(self._frames), sorted({lo, mid, hi}), axis=0)
        self.image = np.ascontiguousarray(part[mid])
        self._lower = cv2.subtract(part[lo], np.full_like(part[lo], self.DIFF))
        self._upper = cv2.add(part[hi], np.full_like(part[hi], self.DIFF))
        self._built_from = n
        if self.complete:
            self._frames = []
        return True

    def changed(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """Mask (255) of pixels outside their usual band, or None when there
        is no model for a frame of this size."""
        lower = self._lower
        if lower is None or lower.shape != frame.shape:
            return None
        return cv2.bitwise_not(cv2.inRange(frame, lower, self._upper))


def find_click_circle(panel: np.ndarray) -> Optional[Tuple[int, int, int]]:
    """Finds the click circle in a fish-free picture of the panel.

    Returns (center_x, center_y, radius) or None. Works for an outline as well
    as for a filled disc. Only a circle around the middle of the panel with
    real edge support along most of its perimeter is accepted, so buttons,
    icons and the panel frame are not mistaken for it."""
    if panel is None or panel.ndim != 3:
        return None
    h, w = panel.shape[:2]
    m = min(h, w)
    if m < 60:
        return None
    gray = cv2.GaussianBlur(cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY), (5, 5), 1.2)
    try:
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=m / 4,
                                   param1=90, param2=24,
                                   minRadius=int(m * 0.15), maxRadius=int(m * 0.5))
    except cv2.error:
        return None
    if circles is None:
        return None

    edges = cv2.dilate(cv2.Canny(gray, 45, 90), np.ones((3, 3), np.uint8), iterations=1)
    angles = np.linspace(0, 2 * np.pi, 96, endpoint=False)
    cos_a, sin_a = np.cos(angles), np.sin(angles)

    best = None
    best_support = 0.7  # share of the perimeter that must lie on an edge
    for cx, cy, r in circles[0][:6]:
        if not (w * 0.25 <= cx <= w * 0.75 and h * 0.25 <= cy <= h * 0.75):
            continue
        xs = np.rint(cx + r * cos_a).astype(int)
        ys = np.rint(cy + r * sin_a).astype(int)
        inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        if inside.sum() < angles.size * 0.9:
            continue
        support = float((edges[ys[inside], xs[inside]] > 0).sum()) / angles.size
        if support > best_support:
            best_support = support
            best = (int(round(cx)), int(round(cy)), int(round(r)))
    return best


class FishDetector:
    """Detects the minigame panel and the fish with HSV thresholds.
    Optimized with cached function references and minimal allocations."""

    # Class-level cached function references for speed
    _cvtColor = cv2.cvtColor
    _countNonZero = cv2.countNonZero
    _findContours = cv2.findContours
    _boundingRect = cv2.boundingRect
    _contourArea = cv2.contourArea
    _connectedComponentsWithStats = cv2.connectedComponentsWithStats
    _COLOR_BGR2HSV = cv2.COLOR_BGR2HSV
    _RETR_EXTERNAL = cv2.RETR_EXTERNAL
    _CHAIN_APPROX_SIMPLE = cv2.CHAIN_APPROX_SIMPLE
    _CC_STAT_AREA = cv2.CC_STAT_AREA

    # Kept for backwards compatibility — the live values come from the profile.
    _WINDOW_PIXEL_THRESHOLD = 10000
    _FISH_PIXEL_THRESHOLD = 24

    # The panel counts as open while it shows at least this share of the
    # pixels it had when it was calibrated.
    _ACTIVE_FRACTION = 0.6

    _OPEN_KERNEL = np.ones((3, 3), np.uint8)

    # Learned click circle: share of the drawn radius that is clicked in.
    _CIRCLE_SAFETY = 0.92
    # A circle found again within this many pixels of the known one is the
    # same circle: the estimate wobbles a little from one look to the next.
    _CIRCLE_TOLERANCE = 5

    # The panel is where it was calibrated while it shows at least this share
    # of the pixels it had then. Less means part of it is outside the region
    # (it opened somewhere else, or was dragged) or something covers it.
    _IN_PLACE_FRACTION = 0.8

    @property
    def partly_visible(self) -> bool:
        """The last frame showed the panel, but clearly not all of it."""
        return 0 < self.window_pixels < self.calibrated_pixels * self._IN_PLACE_FRACTION

    def __init__(self, profile: Optional[DetectionProfile] = None):
        self.profile = DetectionProfile()
        self.window_color_lower = None
        self.window_color_upper = None
        self.fish_color_lower = None
        self.fish_color_upper = None
        self._window_px_threshold = self.profile.window_min_pixels
        self._fish_px_threshold = self.profile.fish_min_pixels
        self._last_bounds_pixels = 0
        self.window_pixels = 0        # panel-colored pixels in the last frame looked at
        self.calibrated_pixels = 0    # ... and in the panel when it was calibrated
        # Learned profiles only: the panel without the fish. Whatever does
        # not differ from it (circle outline, labels, texture) is not a fish.
        self._background: Optional[BackgroundModel] = None
        self._learner: Optional["FishLearner"] = None
        self.profile_updated = False  # set when learning changed the profile
        self.set_profile(profile or self.profile)

    # ------------------------------------------------------------------ profile

    def set_profile(self, profile: DetectionProfile) -> None:
        self.profile = profile
        self.window_color_lower = np.array(profile.window_lower, dtype=np.uint8)
        self.window_color_upper = np.array(profile.window_upper, dtype=np.uint8)
        if profile.fish_lower is not None and profile.fish_upper is not None:
            self.fish_color_lower = np.array(profile.fish_lower, dtype=np.uint8)
            self.fish_color_upper = np.array(profile.fish_upper, dtype=np.uint8)
        else:
            self.fish_color_lower = None
            self.fish_color_upper = None
        self._window_px_threshold = profile.window_min_pixels
        self._fish_px_threshold = profile.fish_min_pixels
        self._background = None
        # A learned profile needs the background model (and maybe the fish
        # color) rebuilt from live frames; the stock profile needs neither.
        self._learner = FishLearner(need_color=self.fish_color_lower is None) if profile.learned else None

    def calibrate(self, bounds_pixels: Optional[int] = None) -> None:
        """Sets the "panel is open" threshold relative to the calibrated panel.

        For a panel as large as the official one this leaves the original
        10000 px threshold untouched; smaller panels get a proportional one."""
        if bounds_pixels is None:
            bounds_pixels = self._last_bounds_pixels
        if bounds_pixels and bounds_pixels > 0:
            self.calibrated_pixels = int(bounds_pixels)
            self._window_px_threshold = max(
                1, min(self.profile.window_min_pixels, int(bounds_pixels * self._ACTIVE_FRACTION)))
        # Region changed: the per-pixel background model no longer lines up.
        self._background = None
        if self._learner is not None:
            self._learner.reset()

    def circle_center_for(self, width: int, height: int) -> Tuple[int, int]:
        """Click-zone center for a panel of this size: the learned one when it
        lies inside the panel, otherwise the middle."""
        center = self.profile.circle_center
        if center is not None and 0 < center[0] < width and 0 < center[1] < height:
            return (int(center[0]), int(center[1]))
        return (width >> 1, height >> 1)

    def learn_fish_by_motion(self) -> None:
        """Keeps the panel colors but re-learns the fish from its movement.
        For clients that use the standard panel with a different fish."""
        old = self.profile
        threshold = self._window_px_threshold
        calibrated = self.calibrated_pixels
        self.set_profile(DetectionProfile(
            name="learned", window_lower=old.window_lower, window_upper=old.window_upper,
            fish_lower=None, fish_upper=None, circle_radius=old.circle_radius,
            circle_center=old.circle_center, window_min_pixels=old.window_min_pixels,
            fish_min_pixels=old.fish_min_pixels, learned=True))
        self._window_px_threshold = threshold
        self.calibrated_pixels = calibrated

    def circle_radius_for(self, width: int, height: int) -> int:
        """Click-zone radius for a panel of this size. The configured radius is
        used as-is unless the panel is too small to contain such a circle."""
        return max(5, min(self.profile.circle_radius, int(min(width, height) * 0.42)))

    @property
    def learning(self) -> bool:
        return self._learner is not None

    # ---------------------------------------------------------------- detection

    def panel_pixels(self, frame: np.ndarray) -> int:
        """Pixels of the active profile's panel color in ``frame``."""
        if frame is None or frame.size == 0 or self.window_color_lower is None:
            return 0
        hsv = FishDetector._cvtColor(frame, FishDetector._COLOR_BGR2HSV)
        return int(FishDetector._countNonZero(_hsv_mask(hsv, self.window_color_lower, self.window_color_upper)))

    def find_fishing_window_bounds(self, frame: np.ndarray, observe_only: bool = False) -> Optional[Bounds]:
        """Finds the bounding box of the fishing panel. Returns (x, y, w, h) or None.
        ``observe_only`` leaves the detector's state untouched (debug views)."""
        hsv = FishDetector._cvtColor(frame, FishDetector._COLOR_BGR2HSV)
        mask = _hsv_mask(hsv, self.window_color_lower, self.window_color_upper)
        outline = mask
        if self.profile.learned:
            # Lines drawn across a re-skinned panel (circle, foam, dividers)
            # must not split it into several pieces.
            outline = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
        contours, _ = FishDetector._findContours(
            outline, FishDetector._RETR_EXTERNAL, FishDetector._CHAIN_APPROX_SIMPLE)

        if not contours:
            return None

        largest_contour = max(contours, key=FishDetector._contourArea)
        x, y, w, h = FishDetector._boundingRect(largest_contour)

        if w > 50 and h > 50:
            if not observe_only:
                self._last_bounds_pixels = FishDetector._countNonZero(mask[y:y + h, x:x + w])
            return (x, y, w, h)
        return None

    def detect_window_and_fish(self, frame: np.ndarray,
                               observe_only: bool = False) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Combined detection: single HSV conversion for both panel and fish.
        Returns: (window_active, fish_position or None)

        ``observe_only`` runs the detection without learning from the frame —
        for debug views, which run on another thread and look at other crops."""
        countNonZero = FishDetector._countNonZero

        hsv = FishDetector._cvtColor(frame, FishDetector._COLOR_BGR2HSV)

        # Panel first — early exit if it is not open.
        window_mask = _hsv_mask(hsv, self.window_color_lower, self.window_color_upper)
        pixels = countNonZero(window_mask)
        if not observe_only:
            self.window_pixels = pixels
        if pixels <= self._window_px_threshold:
            return (False, None)

        learner = None if observe_only else self._learner
        motion_pos = None
        if learner is not None:
            motion_pos = learner.feed(frame, hsv, window_mask)
            if learner.done:
                self._finish_learning(learner)

        if self.fish_color_lower is None:
            # Fish color not known yet: follow the moving blob meanwhile.
            return (True, motion_pos)

        fish_mask = _hsv_mask(hsv, self.fish_color_lower, self.fish_color_upper)
        if self.profile.learned:
            # Learned ranges are wider than hand-tuned ones; keep them off the
            # background and off permanent decorations.
            fish_mask = cv2.bitwise_and(fish_mask, cv2.bitwise_not(window_mask))
            background = self._background
            if background is not None:
                if not background.complete and not observe_only:
                    background.add(frame, time.perf_counter())
                changed = background.changed(frame)
                if changed is not None:
                    fish_mask = cv2.bitwise_and(fish_mask, changed)
                    # An animated background leaves single-pixel sparkle
                    # outside its band; the fish is a solid body.
                    fish_mask = cv2.morphologyEx(fish_mask, cv2.MORPH_OPEN, self._OPEN_KERNEL)

        # Cheap upfront pixel count avoids the connected-components pass
        # (allocates labels + stats + centroids) when only noise matched.
        if countNonZero(fish_mask) < self._fish_px_threshold:
            return (True, None)

        # connectivity=4 is ~1.5x faster than 8 and equivalent for a compact blob.
        num_labels, _, stats, centroids = FishDetector._connectedComponentsWithStats(
            fish_mask, connectivity=4)
        if num_labels <= 1:
            return (True, None)

        # Label 0 is background; largest foreground blob wins.
        areas = stats[1:, FishDetector._CC_STAT_AREA]
        largest_idx = int(areas.argmax()) + 1
        if self.profile.learned and areas[largest_idx - 1] < self._fish_px_threshold:
            return (True, None)
        cx, cy = centroids[largest_idx]
        return (True, (int(cx), int(cy)))

    def _finish_learning(self, learner: "FishLearner") -> None:
        """Adopts what the learner found and stops feeding it."""
        diag.log("ANALYSIS", f"fish learner finished: circle={learner.circle} fish HSV range={learner.fish_range} "
                             f"(profile before: circle {self.profile.circle_center} r={self.profile.circle_radius}, "
                             f"fish {self.profile.fish_lower}-{self.profile.fish_upper})")
        self._background = learner.background
        if learner.circle is not None:
            cx, cy, r = learner.circle
            # Slightly inside the drawn circle: the fish must be clearly in.
            radius = max(5, int(r * self._CIRCLE_SAFETY))
            known = self.profile.circle_center
            tol = self._CIRCLE_TOLERANCE
            same = (known is not None and abs(known[0] - cx) <= tol and abs(known[1] - cy) <= tol
                    and abs(self.profile.circle_radius - radius) <= tol)
            if not same:
                self.profile.circle_center = (cx, cy)
                self.profile.circle_radius = radius
                self.profile_updated = True
        if self.fish_color_lower is None and learner.fish_range is not None:
            lo, hi = learner.fish_range
            self.profile.fish_lower = lo
            self.profile.fish_upper = hi
            self.fish_color_lower = np.array(lo, dtype=np.uint8)
            self.fish_color_upper = np.array(hi, dtype=np.uint8)
            self.profile_updated = True
        self._learner = None


class WindowLearner:
    """Finds a re-skinned minigame panel without knowing its color.

    Compares a frame taken just before the cast with the current frame. The
    minigame is the only thing that appears as a large, dense, rectangular
    block of change with one dominant color; the character's cast animation
    and world movement are small or irregular."""

    DIFF_THRESHOLD = 25
    MIN_W, MIN_H = 120, 100
    MAX_W_FRAC, MAX_H_FRAC = 0.7, 0.8
    MIN_FILL = 0.6        # changed pixels / bounding box
    MIN_COVERAGE = 0.35   # dominant color / bounding box

    def learn(self, reference: np.ndarray, current: np.ndarray):
        """Returns (region_bounds, window_lower, window_upper, pixel_count) or None."""
        if reference is None or current is None or reference.shape != current.shape:
            return None
        frame_h, frame_w = current.shape[:2]

        diff = cv2.absdiff(reference, current).max(axis=2)
        _, changed = cv2.threshold(diff, self.DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(changed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
            x, y, w, h = cv2.boundingRect(contour)
            if w < self.MIN_W or h < self.MIN_H:
                break  # sorted by area — the rest are smaller still
            if w > frame_w * self.MAX_W_FRAC or h > frame_h * self.MAX_H_FRAC:
                continue
            if cv2.countNonZero(changed[y:y + h, x:x + w]) < w * h * self.MIN_FILL:
                continue

            result = self._learn_color(reference, current, (x, y, w, h))
            if result is not None:
                return result
        return None

    def _learn_color(self, reference, current, box: Bounds):
        x, y, w, h = box
        roi_hsv = cv2.cvtColor(current[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)

        cluster = _dominant_cluster(roi_hsv.reshape(-1, 3))
        if cluster.shape[0] < w * h * 0.2:
            return None

        lower, upper = _robust_hsv_range(cluster, 2, 98, h_margin=3, sv_margin=20)
        lower_a = np.array(lower, dtype=np.uint8)
        upper_a = np.array(upper, dtype=np.uint8)

        mask = _hsv_mask(roi_hsv, lower_a, upper_a)
        coverage = cv2.countNonZero(mask)
        if coverage < w * h * self.MIN_COVERAGE:
            return None

        # The color must be new: if the same spot already showed it before the
        # cast, this is scenery, not a panel that just opened.
        ref_hsv = cv2.cvtColor(reference[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)
        if cv2.countNonZero(_hsv_mask(ref_hsv, lower_a, upper_a)) > coverage * 0.5:
            return None

        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        rx, ry, rw, rh = cv2.boundingRect(max(contours, key=cv2.contourArea))
        if rw <= 50 or rh <= 50:
            return None
        pixel_count = cv2.countNonZero(mask[ry:ry + rh, rx:rx + rw])
        return ((x + rx, y + ry, rw, rh), lower, upper, pixel_count)


class FishLearner:
    """Learns what the fish looks like from motion alone.

    Fed with successive frames of the calibrated panel, it
    - tracks the moving non-background blob (usable as the fish position
      right away, before any color is known),
    - collects that blob's colors until a stable HSV range can be derived,
    - samples the panel into a ``BackgroundModel`` (what never changes:
      circle outline, text, texture), so the fish mask can ignore all of it,
    - locates the click circle in that model.
    """

    MOTION_THRESHOLD = 18
    BASELINE_AGE = 0.06     # compare against a frame at least this old (s)
    MIN_FRAMES = 8          # frames with a usable fish blob
    MIN_SAMPLES = 400       # fish pixels collected
    MIN_DURATION = 0.6      # seconds of observation before trusting "static"
    MAX_SAMPLES = 20000
    MIN_MOVING_PIXELS = 10  # motion on a body below this is noise
    # A full square: an elliptical kernel still fits into a 4 px wide diagonal
    # line (foam on animated water), which then passes for a small body.
    _BODY_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    def __init__(self, need_color: bool = True):
        self.need_color = need_color
        self.reset()

    def reset(self) -> None:
        self._history: Deque[Tuple[float, np.ndarray]] = deque(maxlen=64)
        self._samples: List[np.ndarray] = []
        self._sample_count = 0
        self._blob_frames = 0
        # Color ranges that turned out not to be the fish (see _plausible_fish)
        self._excluded: List[Tuple[np.ndarray, np.ndarray]] = []
        # A few panel pictures (HSV) spread over the last second or so
        self._recent: Deque[np.ndarray] = deque(maxlen=10)
        self._recent_at = float("-inf")
        self._shape: Optional[Tuple[int, int]] = None
        self._t0: Optional[float] = None
        self.fish_range: Optional[Tuple[HSV, HSV]] = None
        self.background = BackgroundModel()
        self.circle: Optional[Tuple[int, int, int]] = None  # (cx, cy, r) in the panel
        self.done = False

    def feed(self, frame: np.ndarray, hsv: np.ndarray, window_mask: np.ndarray,
             now: Optional[float] = None) -> Optional[Tuple[int, int]]:
        """Consumes one panel frame. Returns the moving blob's position or None."""
        if now is None:
            now = time.perf_counter()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        if self._shape != gray.shape:
            self.reset()
            self._shape = gray.shape
            self._t0 = now

        nonbg = cv2.bitwise_not(window_mask)
        for lo, hi in self._excluded:
            nonbg = cv2.bitwise_and(nonbg, cv2.bitwise_not(_hsv_mask(hsv, lo, hi)))
        self.background.add(frame, now)
        if now - self._recent_at >= 0.1:
            self._recent_at = now
            self._recent.append(hsv.copy())

        # Baseline: the newest stored frame that is old enough for the fish to
        # have visibly moved. Consecutive captures are only a few ms apart.
        baseline = None
        for t, g in reversed(self._history):
            if now - t >= self.BASELINE_AGE:
                baseline = g
                break
        self._history.append((now, gray))
        while self._history and now - self._history[0][0] > 0.5:
            self._history.popleft()

        pos = None
        if baseline is not None:
            # Large things that are always there but are not panel-colored (a
            # filled click zone, a picture) would swallow a fish that swims
            # over them: once there is a background model, only what differs
            # from it can be the fish.
            changed = self.background.changed(frame)
            if changed is not None:
                nonbg = cv2.bitwise_and(nonbg, changed)
            pos = self._track(gray, baseline, nonbg, hsv)

        self._maybe_finish(now)
        return pos

    def _track(self, gray, baseline, nonbg, hsv) -> Optional[Tuple[int, int]]:
        """Finds the fish: the solid non-background body that is moving.

        Candidates are whole bodies (connected non-background areas after
        thin structures — outlines, foam lines, text strokes — are removed),
        ranked by how much of the frame-to-frame motion falls on them. That
        keeps static decorations (no motion), a shrinking timer bar (long and
        thin) and background animation (not a body) from being followed."""
        h, w = gray.shape
        motion = cv2.absdiff(gray, baseline)
        _, motion = cv2.threshold(motion, self.MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)

        bodies = cv2.morphologyEx(nonbg, cv2.MORPH_OPEN, self._BODY_KERNEL)
        # Ignore the panel's border (frame art, anti-aliased edges)
        m = max(2, int(min(h, w) * 0.04))
        bodies[:m, :] = 0
        bodies[-m:, :] = 0
        bodies[:, :m] = 0
        bodies[:, -m:] = 0

        n, labels, stats, centroids = cv2.connectedComponentsWithStats(bodies, connectivity=8)
        if n <= 1:
            return None

        # Motion pixels per body (label 0 = background)
        moving = np.bincount(labels[motion > 0], minlength=n)
        max_area = h * w * 0.15
        best = -1
        best_moving = self.MIN_MOVING_PIXELS - 1
        for i in range(1, n):
            if moving[i] <= best_moving:
                continue
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area < 20 or area > max_area:
                continue
            bw = int(stats[i, cv2.CC_STAT_WIDTH])
            bh = int(stats[i, cv2.CC_STAT_HEIGHT])
            if area < bw * bh * 0.25:
                continue  # ring / outline, not a body
            if max(bw, bh) > 6 * min(bw, bh):
                continue  # bar or line
            best, best_moving = i, int(moving[i])
        if best < 0:
            return None

        if self.need_color and self._sample_count < self.MAX_SAMPLES:
            px = hsv[labels == best]
            self._samples.append(px)
            self._sample_count += px.shape[0]
        self._blob_frames += 1

        cx, cy = centroids[best]
        return (int(cx), int(cy))

    def _plausible_fish(self, color_range: Tuple[HSV, HSV]) -> Optional[bool]:
        """Whether a color range behaves like one fish in the recent frames.

        The fish is a single compact body. A color that sits in many places
        at once (foam lines on animated water) is not it: False. None when
        the frames do not say — too few of them, or the color did not move
        in them (a fish that was resting, or a decoration)."""
        frames = list(self._recent)
        if len(frames) < 5:
            return None
        lo = np.array(color_range[0], dtype=np.uint8)
        hi = np.array(color_range[1], dtype=np.uint8)
        masks = [_hsv_mask(f, lo, hi) for f in frames]
        # Pixels that have the color in nearly every frame are scenery
        always = (np.sum([m > 0 for m in masks], axis=0) >= len(masks) * 0.8)
        shares = []
        for m in masks:
            moving = m.copy()
            moving[always] = 0
            total = cv2.countNonZero(moving)
            if total < self.MIN_MOVING_PIXELS:
                continue
            n, _, stats, _ = cv2.connectedComponentsWithStats(moving, connectivity=8)
            shares.append(float(stats[1:, cv2.CC_STAT_AREA].max()) / total if n > 1 else 0.0)
        if len(shares) < 3:
            return None
        return float(np.median(shares)) >= 0.6

    def _maybe_finish(self, now: float) -> None:
        if self.done or now - self._t0 < self.MIN_DURATION:
            return
        if self.need_color:
            if self._blob_frames < self.MIN_FRAMES or self._sample_count < self.MIN_SAMPLES:
                return
            pixels = np.concatenate(self._samples, axis=0)
            # The fish's main color: what was sampled can include a few other
            # things that moved (effects, background animation).
            cluster = _dominant_cluster(pixels)
            if cluster.shape[0] < self.MIN_SAMPLES * 0.5:
                return
            if self.background.count < 5:
                return
            candidate = _robust_hsv_range(cluster, 2, 98, h_margin=4, sv_margin=18)
            verdict = self._plausible_fish(candidate)
            if verdict is not True:
                if verdict is False:
                    # Something else that moves (foam, sparkle, an effect) was
                    # followed. Leave that color out and keep looking.
                    self._excluded.append((np.array(candidate[0], dtype=np.uint8),
                                           np.array(candidate[1], dtype=np.uint8)))
                # (None: cannot tell yet — look again with fresh samples.)
                self._samples = []
                self._sample_count = 0
                self._blob_frames = 0
                return
            self.fish_range = candidate
        elif self._blob_frames < self.MIN_FRAMES:
            # Not enough movement seen yet to tell static from moving.
            return

        if self.background.count < 5 or not self.background.build():
            return
        self.circle = find_click_circle(self.background.image)
        self._samples = []
        self._history.clear()
        self.done = True


# Diagnostic log: entry/exit of the detector's methods (per-frame calls only
# with "Verbose"; no-op wrappers while diagnostics is off).
diag.trace_class(FishDetector)
diag.trace_class(WindowLearner)
diag.trace_class(FishLearner, exclude=("_track",))
diag.trace_class(BackgroundModel, exclude=("add", "changed"))
