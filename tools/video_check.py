"""
Runs the bot's detection over a screen recording of the game and reports
what it would have seen. Nothing is clicked: this only reads the video.

    python tools/video_check.py recording.mp4
    python tools/video_check.py recording.mp4 --game 0,0,1280,720 \
        --tabs "1123,345 1155,345 1187,345 1219,345 1251,345" --out check/

--game   where the game window is in the video (x,y,w,h; default: the whole
         frame), in video pixels before --scale.
--scale  how much the video enlarged the game (e.g. 1.5 for a 1280x720 game
         uploaded as 1080p): frames are shrunk by it first, so the bot's
         code sees the game at 1:1.
--start/--end   seconds of the video to look at.
--tabs   the inventory page tabs in game-window coordinates, as the bot's
         setup would capture them. Without it the inventory is not checked.
--out    folder for contact sheets of the frames worth a look.

Fishing minigame, per frame: is the panel found, where, how big; where is
the fish; and two independent opinions to compare with:
  * the ring color: the official client draws the ring red while the game
    counts the fish as inside (it lags about one frame, and counts a fish
    that touches the ring);
  * a "dark blob on the water" detector that knows nothing about colors.
Frames where the bot sees no fish but the dark detector finds one inside
the circle are listed: the fish was missed (or it is a "Hit"/"Miss" text).

Inventory (with --tabs): strip position, active tab and empty slots, every
15th frame; changes are printed.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# The test stubs stand in for the input and window modules: this script never
# touches the desktop, it only runs the detection code on video frames.
from _stubs import install, FakeWindowManager  # noqa: E402
install()

from fish_detector import FishDetector  # noqa: E402


def ring_color(region, center, radius):
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h, w = region.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - center[0], yy - center[1])
    ring = (d > radius - 15) & (d < radius + 3)
    hh, ss, vv = hsv[..., 0][ring], hsv[..., 1][ring], hsv[..., 2][ring]
    # Red on a 1:1 client (hue ~170, saturation ~100); a pale pink (hue ~153,
    # saturation ~54) once the video was scaled and blended with the water.
    red = int((((hh < 15) | (hh > 140)) & (ss > 35) & (vv > 150)).sum())
    white = int(((ss < 30) & (vv > 200)).sum())
    if red > 300 and red > white:
        return "red"
    return "white" if white > 300 else "?"


def dark_blob(region):
    """Largest dark blob on the water (frame, counter and timer left out)."""
    h, w = region.shape[:2]
    v = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)[..., 2]
    bg = cv2.medianBlur(v, 31).astype(np.int16)
    m = ((bg - v.astype(np.int16)) > 35).astype(np.uint8)
    m[:, :14] = 0
    m[:, w - 14:] = 0
    m[:12] = 0
    m[:42, w - 90:] = 0
    m[h - 35:] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return None
    k = 1 + int(st[1:, cv2.CC_STAT_AREA].argmax())
    if st[k, cv2.CC_STAT_AREA] < 40:
        return None
    return int(round(cen[k][0])), int(round(cen[k][1]))


def sheet(path, tiles, cols=5):
    if not tiles:
        return
    th, tw = max(t.shape[0] for t in tiles), max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, tw - t.shape[1], cv2.BORDER_CONSTANT) for t in tiles]
    tiles += [np.zeros_like(tiles[0])] * (-len(tiles) % cols)
    cv2.imwrite(path, np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--game", help="x,y,w,h of the game window in the video")
    ap.add_argument("--tabs", help='inventory tabs, "x,y x,y ..." in game-window coordinates')
    ap.add_argument("--scale", type=float, default=1.0, help="video pixels per game pixel")
    ap.add_argument("--start", type=float, default=0.0, help="first second to look at")
    ap.add_argument("--end", type=float, default=None, help="last second to look at")
    ap.add_argument("--out", help="folder for contact sheets")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"cannot open {args.video}")
    fw, fh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    gx, gy, gw, gh = [int(v) for v in args.game.split(",")] if args.game else (0, 0, fw, fh)
    scale = args.scale
    gw_s, gh_s = int(round(gw / scale)), int(round(gh / scale))
    if args.start:
        cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    tabs = None
    if args.tabs:
        tabs = {i + 1: tuple(int(v) for v in p.split(",")) for i, p in enumerate(args.tabs.split())}
        import inventory_layout
        from fishing_bot import FishingBot
        bot = FishingBot(None, {}, FakeWindowManager(gw, gh), bait_counter=0, bot_id=0)
        cfg = {f"inv_page_{p}_pos": list(v) for p, v in tabs.items()}
        inv_rect, inv_source = inventory_layout.strip_rect(cfg, gw_s, gh_s)
        print(f"inventory strip {inv_rect} (from {inv_source})")
    print(f"video {fw}x{fh} @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps, {int(cap.get(cv2.CAP_PROP_FRAME_COUNT))} frames; "
          f"game window {gx},{gy} {gw}x{gh}" + (f", shrunk {scale:g}x to {gw_s}x{gh_s}" if scale != 1 else ""))

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    def sec(n):
        return f"{args.start + n / fps:.2f}s"

    det = FishDetector()
    stats = dict(panel=0, fish=0, ring_red=0, red_and_bot_in=0, red_bot_no_fish=0, bot_in_white=0)
    heights, games, missed = [], [], []
    open_since, last_bounds, last_inv = None, None, None
    i = 0
    while True:
        if args.end is not None and cap.get(cv2.CAP_PROP_POS_MSEC) > args.end * 1000:
            break
        ok, frame = cap.read()
        if not ok:
            break
        game = frame[gy:gy + gh, gx:gx + gw]
        if scale != 1:
            game = cv2.resize(game, (gw_s, gh_s), interpolation=cv2.INTER_AREA)
        bounds = det.find_fishing_window_bounds(game, observe_only=True)
        if bounds and open_since is None:
            open_since = i
        if not bounds and open_since is not None:
            games.append((open_since, i - 1, last_bounds))
            open_since = None
        if bounds:
            last_bounds = bounds
            stats["panel"] += 1
            x, y, w, h = bounds
            heights.append(h)
            region = game[y:y + h, x:x + w]
            _, fish = det.detect_window_and_fish(region, observe_only=True)
            center, radius = det.circle_center_for(w, h), det.circle_radius_for(w, h)
            ring = ring_color(region, center, radius)
            inside = fish is not None and np.hypot(fish[0] - center[0], fish[1] - center[1]) < radius
            stats["fish"] += fish is not None
            if ring == "red":
                stats["ring_red"] += 1
                stats["red_and_bot_in"] += inside
                stats["red_bot_no_fish"] += fish is None
            elif ring == "white" and inside:
                stats["bot_in_white"] += 1
            if fish is None:
                blob = dark_blob(region)
                if blob is not None and np.hypot(blob[0] - center[0], blob[1] - center[1]) < radius - 6:
                    missed.append((i, region.copy(), blob))
        if tabs and i % 15 == 0:
            x, y, w, h = inv_rect
            strip = np.ascontiguousarray(game[y:y + h, x:x + w])
            slots = bot._scan_empty_slots(strip)
            active, _ = inventory_layout.active_tab(game, (0, 0), tabs)
            state = (active, tuple(slots))
            if state != last_inv:
                print(f"frame {i} ({sec(i)}): active tab {active}, {len(slots)} empty slots "
                      f"{[(sx + x, sy + y) for sx, sy in slots]}")
                last_inv = state
        i += 1
    if open_since is not None:
        games.append((open_since, i - 1, last_bounds))

    print(f"\nminigame panel in {stats['panel']} of {i} frames, {len(games)} times:")
    for a, b, bd in games:
        print(f"  frames {a}-{b} ({sec(a)}-{sec(b)})  last bounds {bd}")
    if heights:
        print(f"panel height measured {min(heights)}-{max(heights)} px (median {int(np.median(heights))}); "
              f"the circle sits at half the calibrated height")
    if stats["panel"]:
        print(f"fish found in {stats['fish']} panel frames ({stats['fish'] / stats['panel']:.0%})")
        print(f"ring red (game: fish in) {stats['ring_red']} frames: bot also in {stats['red_and_bot_in']}, "
              f"bot saw no fish {stats['red_bot_no_fish']}")
        print(f"bot 'in' while the ring was white: {stats['bot_in_white']} frames "
              f"(a click there may be early; the ring lags ~1 frame)")
    print(f"dark blob inside the circle, bot saw no fish: {len(missed)} frames"
          + (": " + " ".join(str(m[0]) for m in missed[:60]) if missed else ""))
    if args.out and missed:
        os.makedirs(args.out, exist_ok=True)
        tiles = []
        for n, region, (bx, by) in missed[::max(1, len(missed) // 40)]:
            t = region.copy()
            cv2.circle(t, (bx, by), 12, (0, 255, 0), 1)
            cv2.putText(t, sec(n), (4, 16), 0, 0.5, (0, 255, 255), 1)
            tiles.append(t)
        path = os.path.join(args.out, "missed_fish.jpg")
        sheet(path, tiles)
        print(f"contact sheet: {path}")


if __name__ == "__main__":
    main()
