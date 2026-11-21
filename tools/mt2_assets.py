"""Real game textures for the simulator and the bot, from a Metin2 client.

The server owner allowed the textures used here to be published in this
repository.

Two sources:

``packs``   the client's own files (``data/pack/*.to``): the fishing window's
            texture atlas and its layout script (``uiscript``), and the
            inventory icons. Packs are EterPack: every entry is ``MCOZ`` +
            LZO1X (none of these is encrypted). The index file (``.eix``, renamed by
            most servers) that
            names them is encrypted with the server's key, so entries are
            found by their content instead (carving).
``capture`` a diagnostics zip of a real session (lossless PNG shots and the
            10 fps recording): what is drawn on screen but not stored as one
            texture: the window frame, an empty inventory slot, the "n/3"
            counter text (the font is the system's), and test fixtures.

    python tools/mt2_assets.py packs <client folder>
    python tools/mt2_assets.py capture diagnostics/20261120_195925.zip
    python tools/mt2_assets.py templates <client folder>   (rewrites assets/fishing)

``templates`` puts the exact game icon of every item the bot knows into
assets/fishing, drawn on the real empty slot (the game blends icons over the
slot with their alpha: tiles match the screen to 0.2 on average). File names
stay as they are (they are the item names in configs, the GUI and the tests);
JPEG files are written at quality 100 without chroma subsampling. Each icon is
found by its closest match to the file's current picture, or for a living
fish to ``fish_states/<Fish>_living.png`` (the website's Carp_living.jpg is
closer to the game's dead Carp). Run ``capture`` first (it makes the slot).
"""

import argparse
import glob
import io
import os
import re
import struct
import sys
import zipfile

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_ASSETS = os.path.join(ROOT, "tools", "minigame_sim", "assets", "real_mt2")
BOT_ASSETS = os.path.join(ROOT, "assets", "fishing")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "real_mt2")

# Where the diagnostics session 20261120_195925 (800x600 client) shows things
WATER_ORIGIN = (261, 197)          # the window's water image, in the client
BOARD = (7, 29)                    # water origin in the window (uiscript)
WINDOW_SIZE = (292, 263)           # 278 + 14, 226 + 37 (uiscript)
SLOT_STRIP = (110, 5)              # inventory grid in the strip shots (x, y of slot 0)


# ------------------------------------------------------------------ EterPack

def lzo1x_decompress(src):
    """LZO1X decompression (a port of minilzo's state machine)."""
    dst = bytearray()
    ip = 0

    def literal(k):
        nonlocal ip
        dst.extend(src[ip:ip + k])
        ip += k

    def copy(dist, length):
        p = len(dst) - dist
        if p < 0:
            raise ValueError("LZO: lookbehind before the start")
        if dist >= length:
            dst.extend(dst[p:p + length])
        else:
            for i in range(length):
                dst.append(dst[p + i])

    LOOP, FIRST_LITERAL, MATCH, MATCH_DONE, MATCH_NEXT = range(5)
    t = 0
    if src[0] > 17:
        t = src[0] - 17
        ip = 1
        if t < 4:
            state = MATCH_NEXT
        else:
            literal(t)
            state = FIRST_LITERAL
    else:
        state = LOOP
    while True:
        if state == LOOP:
            t = src[ip]
            ip += 1
            if t >= 16:
                state = MATCH
                continue
            if t == 0:
                while src[ip] == 0:
                    t += 255
                    ip += 1
                t += 15 + src[ip]
                ip += 1
            literal(t + 3)
            state = FIRST_LITERAL
        elif state == FIRST_LITERAL:
            t = src[ip]
            ip += 1
            if t >= 16:
                state = MATCH
                continue
            copy(1 + 0x800 + (t >> 2) + (src[ip] << 2), 3)
            ip += 1
            state = MATCH_DONE
        elif state == MATCH:
            if t >= 64:
                copy(1 + ((t >> 2) & 7) + (src[ip] << 3), (t >> 5) + 1)
                ip += 1
            elif t >= 32:
                length = t & 31
                if length == 0:
                    while src[ip] == 0:
                        length += 255
                        ip += 1
                    length += 31 + src[ip]
                    ip += 1
                v = src[ip] | (src[ip + 1] << 8)
                ip += 2
                copy(1 + (v >> 2), length + 2)
            elif t >= 16:
                dist = (t & 8) << 11
                length = t & 7
                if length == 0:
                    while src[ip] == 0:
                        length += 255
                        ip += 1
                    length += 7 + src[ip]
                    ip += 1
                v = src[ip] | (src[ip + 1] << 8)
                ip += 2
                dist += v >> 2
                if dist == 0:
                    break                       # end of stream
                copy(dist + 0x4000, length + 2)
            else:
                copy(1 + (t >> 2) + (src[ip] << 2), 2)
                ip += 1
            state = MATCH_DONE
        elif state == MATCH_DONE:
            t = src[ip - 2] & 3
            state = LOOP if t == 0 else MATCH_NEXT
        else:                                   # MATCH_NEXT
            literal(t)
            t = src[ip]
            ip += 1
            state = MATCH
    return bytes(dst)


def pack_entries(path):
    """Every unencrypted entry of an EterPack data file, in file order.
    ``MCOZ`` + (encrypted size, compressed size, real size) + ``MCOZ`` + LZO.
    Stray ``MCOZ`` bytes inside other data are skipped by their sizes."""
    data = open(path, "rb").read()
    i = 0
    while True:
        i = data.find(b"MCOZ", i)
        if i < 0:
            return
        _, enc, comp, real = struct.unpack_from("<4sIII", data, i)
        if enc or not comp or not real or real > 64 << 20 or data[i + 16:i + 20] != b"MCOZ":
            i += 4
            continue
        try:
            out = lzo1x_decompress(data[i + 20:i + 20 + comp])
        except (IndexError, ValueError):
            i += 4
            continue
        if len(out) == real:
            yield out
            i += 20 + comp
        else:
            i += 4


def _image(blob):
    """RGBA array of a DDS / TGA / PNG entry, or None."""
    is_tga = len(blob) > 18 and blob[1] == 0 and blob[2] in (2, 10) and blob[16] in (24, 32)
    if not (blob[:4] == b"DDS " or blob[:8] == b"\x89PNG\r\n\x1a\n" or is_tga):
        return None
    try:
        return np.array(Image.open(io.BytesIO(blob)).convert("RGBA"))
    except Exception:
        return None


def _bgra(rgba):
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


# --------------------------------------------------------------------- packs

def extract_packs(game_dir):
    """The fishing atlas and the window's uiscript, into SIM_ASSETS."""
    pack = os.path.join(game_dir, "data", "pack")
    os.makedirs(SIM_ASSETS, exist_ok=True)
    atlas = None
    for blob in pack_entries(os.path.join(pack, "ui.to")):
        if blob[:4] != b"DDS " or struct.unpack_from("<II", blob, 12) != (456, 280):
            continue
        img = _image(blob)
        # Two looks share the layout; the client uses the yellow timer gauge
        r, g, b, _ = img[298, 100].astype(int)
        if r > 150 and g > 150 and b < 110:
            atlas = img
            break
    if atlas is None:
        sys.exit("fishing atlas (280x456 DDS, yellow gauge) not found in ui.to")
    cv2.imwrite(os.path.join(SIM_ASSETS, "fishing_atlas.png"), _bgra(atlas))
    script = None
    for blob in pack_entries(os.path.join(pack, "root.to")):
        if b'"name" : "FishingGameWindow"' in blob:
            script = blob.decode("cp949", errors="replace")
            break
    if script is None:
        sys.exit("FishingGameWindow uiscript not found in root.to")
    with open(os.path.join(SIM_ASSETS, "fishinggamewindow.py.txt"), "w", encoding="utf-8") as f:
        f.write(script)
    print("atlas + uiscript ->", SIM_ASSETS)


def game_icons(game_dir):
    """All 32x32 inventory icons of icon.to (RGBA)."""
    icons = []
    for blob in pack_entries(os.path.join(game_dir, "data", "pack", "icon.to")):
        img = _image(blob)
        if img is not None and img.shape[:2] == (32, 32):
            icons.append(img)
    return icons


# ------------------------------------------------------------------- capture

def _read_zip_png(z, name):
    return cv2.imdecode(np.frombuffer(z.read(name), np.uint8), cv2.IMREAD_COLOR)


def extract_capture(diag_zip):
    """Window frame, empty slot, counter text and fixtures from a session's
    lossless shots and recording."""
    os.makedirs(SIM_ASSETS, exist_ok=True)
    os.makedirs(FIXTURES, exist_ok=True)
    z = zipfile.ZipFile(diag_zip)
    names = z.namelist()
    shot_name = next(n for n in names if re.search(r"_W1_first_minigame_calibrated\.png$", n))
    strip_name = next(n for n in names if re.search(r"_W1_expected_item_not_found\.png$", n))
    shot = _read_zip_png(z, shot_name)
    strip = _read_zip_png(z, strip_name)

    wx, wy = WATER_ORIGIN
    bx, by = wx - BOARD[0], wy - BOARD[1]
    w, h = WINDOW_SIZE
    window = shot[by:by + h, bx:bx + w].copy()
    cv2.imwrite(os.path.join(FIXTURES, "fishing_window.png"), window)      # the real thing, for tests
    board = cv2.cvtColor(window, cv2.COLOR_BGR2BGRA)
    board[BOARD[1]:BOARD[1] + 226, BOARD[0]:BOARD[0] + 278] = 0             # the water is drawn by the sim
    cv2.imwrite(os.path.join(SIM_ASSETS, "board.png"), board)

    # Empty slot: the median of the empty slots of rows 6-8 (all equal to 0.3)
    sx, sy = SLOT_STRIP
    cells = [strip[sy + r * 32:sy + r * 32 + 32, sx + c * 32:sx + c * 32 + 32] for r in range(5, 8) for c in range(5)]
    slot = np.median(np.stack(cells), 0).round().astype(np.uint8)
    cv2.imwrite(os.path.join(SIM_ASSETS, "slot.png"), slot)
    cv2.imwrite(os.path.join(FIXTURES, "inventory_strip.png"), strip)

    # Counter text "n/3": n=0 from the lossless shot, 1 and 2 from the
    # recording (median of every frame showing it bright, which removes the
    # JPEG noise). Only the text area (x 30-66 of the 70x28 counter).
    cx, cy = wx + 205, wy + 5
    texts = {0: shot[cy:cy + 28, cx + 30:cx + 66]}
    stream = next((n for n in names if n.endswith("screen_W1_001.mjpg")), None)
    if stream:
        csv_rows = z.read(stream[:-5] + ".csv").decode().splitlines()[1:]
        atlas = cv2.imread(os.path.join(SIM_ASSETS, "fishing_atlas.png"), cv2.IMREAD_UNCHANGED)
        clock = atlas[258:282, 2:28, :3]
        counter_icon = (np.abs(np.hypot(*(np.mgrid[0:28, 0:28] - 13.5)) - 12.5) < 1.5)
        icon_ref = None
        groups = {}
        raw = z.open(stream)                                # seekable: stored uncompressed
        for row in csv_rows[:4000]:
            parts = row.split(",")
            raw.seek(int(parts[5]))
            frame = cv2.imdecode(np.frombuffer(raw.read(int(parts[6])), np.uint8), cv2.IMREAD_COLOR)
            if frame is None or frame.shape[:2] != (600, 800):
                continue
            c = frame[wy + 196:wy + 220, wx + 30:wx + 56]
            if cv2.matchTemplate(c, clock, cv2.TM_CCOEFF_NORMED)[0, 0] < 0.8:
                continue                                    # no fishing window
            counter = frame[cy:cy + 28, cx:cx + 70].astype(np.float32)
            bright = counter[:, :28][counter_icon].mean()
            if icon_ref is None:
                icon_ref = shot[cy:cy + 28, cx:cx + 28].astype(np.float32)[counter_icon].mean()
            if bright < 0.8 * icon_ref:
                continue                                    # dimmed right after a hit
            digit = counter[7:21, 34:44].mean(2)
            zero = texts[0][7:21, 4:14].astype(np.float32).mean(2)
            if np.abs(digit - zero).mean() < 12:
                continue
            for key, (ref, members) in groups.items():
                if np.abs(ref - digit).mean() < 12:
                    members.append(counter[:, 30:66])
                    break
            else:
                groups[len(groups)] = (digit, [counter[:, 30:66]])
        # the two largest groups are "1/3" and "2/3"
        found = sorted(groups.values(), key=lambda g: -len(g[1]))[:2]
        medians = [np.median(np.stack(m), 0).round().astype(np.uint8) for _, m in found]
        # "1" has fewer bright pixels than "2"
        medians.sort(key=lambda m: (m[7:21, 4:14].mean(2) > 150).sum())
        texts[1], texts[2] = medians
    for n, img in texts.items():
        cv2.imwrite(os.path.join(SIM_ASSETS, f"counter_{n}.png"), img)
    print("board, slot, counter text ->", SIM_ASSETS, "; fixtures ->", FIXTURES)


# ----------------------------------------------------------------- templates

def _on_slot(icon, slot):
    alpha = icon[..., 3:].astype(np.float32) / 255
    rgb = cv2.cvtColor(icon[..., :3], cv2.COLOR_RGB2BGR).astype(np.float32)
    return (rgb * alpha + slot.astype(np.float32) * (1 - alpha)).round().astype(np.uint8)


def _closest(reference, icons):
    """(index, error) of the icon nearest ``reference`` (BGR, about 32x32) on
    its opaque pixels, allowing a shift of up to 2 px."""
    pad = cv2.copyMakeBorder(reference.astype(np.float32), 3, 3, 3, 3, cv2.BORDER_REPLICATE)
    stack = np.stack([cv2.cvtColor(i[..., :3], cv2.COLOR_RGB2BGR) for i in icons]).astype(np.float32)
    masks = np.stack([i[..., 3] > 128 for i in icons])
    best = (None, 1e9)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            crop = pad[3 + dy:3 + dy + 32, 3 + dx:3 + dx + 32]
            if crop.shape[:2] != (32, 32):
                crop = cv2.resize(crop, (32, 32))
            err = (np.abs(stack - crop[None]).mean(3) * masks).sum((1, 2)) / np.maximum(masks.sum((1, 2)), 1)
            k = int(err.argmin())
            if err[k] < best[1]:
                best = (k, float(err[k]))
    return best


def write_templates(game_dir, max_error=20.0):
    slot_path = os.path.join(SIM_ASSETS, "slot.png")
    slot = cv2.imread(slot_path)
    if slot is None:
        sys.exit("run 'capture' first: no " + slot_path)
    icons = game_icons(game_dir)
    report = {}
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(BOT_ASSETS, "*_*.*"))
                   if re.search(r"_(living|item|dead|grilled)\.(jpg|png)$", p))
    names += sorted("fish_states/" + os.path.basename(p)
                    for p in glob.glob(os.path.join(BOT_ASSETS, "fish_states", "*_living.png")))
    for name in names:
        path = os.path.join(BOT_ASSETS, name)
        ref_path = path
        if name.endswith("_living.jpg"):
            exact = os.path.join(BOT_ASSETS, "fish_states", name[:-4] + ".png")
            if os.path.exists(exact):
                ref_path = exact
        reference = cv2.imread(ref_path)
        if reference is None or max(reference.shape[:2]) > 40:
            continue
        k, err = _closest(reference, icons)
        if err > max_error:
            report[name] = f"kept: no game icon within {max_error:g} (best {err:.1f})"
            continue
        tile = _on_slot(icons[k], slot)
        if name.endswith(".jpg"):
            cv2.imwrite(path, tile, [cv2.IMWRITE_JPEG_QUALITY, 100, cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                                     cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444])
        else:
            cv2.imwrite(path, tile)
        stored = cv2.imread(path).astype(np.float32)
        report[name] = f"game icon (error to old {err:.1f}; stored vs exact {np.abs(stored - tile).mean():.2f})"
    cv2.imwrite(os.path.join(BOT_ASSETS, "empty_slot.jpg"), slot,
                [cv2.IMWRITE_JPEG_QUALITY, 100, cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                 cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444])
    report["empty_slot.jpg"] = "real empty slot"
    for k, v in report.items():
        print(f"{k:38s} {v}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("packs").add_argument("game_dir")
    sub.add_parser("capture").add_argument("diag_zip")
    sub.add_parser("templates").add_argument("game_dir")
    args = ap.parse_args(argv)
    if args.cmd == "packs":
        extract_packs(os.path.expanduser(args.game_dir))
    elif args.cmd == "capture":
        extract_capture(os.path.expanduser(args.diag_zip))
    else:
        write_templates(os.path.expanduser(args.game_dir))


if __name__ == "__main__":
    main()
