#!/usr/bin/env python3
"""Cuts the trailer (2560x1440@60) from the takes (takes.py), the intro/dance shots and the music.

    python3 tools/trailer/edit.py [--take v2] [--out FILE]

The desktop recordings are shown 1:1: all 4 clients and the bot window. The
explanations go in a panel under the bot window (big text), every step the
cursor points at gets a yellow outline (the driver's "hl" events), each part
opens with a short title card, then: the setup at real speed, 4 clients at
real speed, a timelapse with the bot's own counters. The end card lies over
the dancing Macaquini, blurred. Part lengths are whole bars of the music.
(edit_v1.py is the first, 1080p cut.)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import music  # noqa: E402

FISH = Path.home() / "Documents/Github/metin2-local-server/modding/out/video/fishbot"
RAW, WORK = FISH / "raw", FISH / "work" / "edit2"
W, H, FPS = 2560, 1440, 60
BAR = music.BAR
ACCENT = "&H3FC8FF&"                    # warm yellow (ASS is BGR)
PANEL = (1632, 744, 920, 660)           # under the bot window (GUI at 1632,60 920x664)

TEXT = {
    "classic": {
        "windows": ("Pick your game windows", "One row per Metin2 client. Here: 4 clients at once."),
        "pages": ("Inventory pages", "Show the bot where the inventory page tabs are, so it can switch pages when one fills up."),
        "page1": ("Inventory pages", "Click Page 1 in the bot, then the inventory tab  I  in the game."),
        "page2": ("Inventory pages", "Same for Page 2: click it, then tab  II  in the game."),
        "classic": ("Classic fishing", "For classic servers, the ones without the fishing minigame: the bot waits for the bite bubble and reels in."),
        "bait": ("Bait keys", "Tick the hotbar keys that hold your bait."),
        "bait_hotbar": ("Bait keys", "Here: 4 stacks of worms on keys 1-4, in every client."),
        "start": ("Start", "Once everything is set, press START. That's it."),
    },
    "minigame": {
        "kept": ("Same windows, same pages", "Set once in the classic part, kept for every mode."),
        "classic_off": ("Fishing minigame", "On servers with the minigame, leave Classic off: the bot plays the minigame and clicks the fish."),
        "quickskip": ("Quick skip", "After each catch the bot hops on the mount and off again (Ctrl+G). That cancels the catch animation, so it casts again sooner."),
        "quickskip_item": ("Quick skip", "Needs a horse. Here every Macaquini has one summoned (the icon top left)."),
        "fishhandling": ("Automatic fish handling", "Decide per fish what happens to it: keep, open or drop."),
        "fishmodal": ("Fish & item selection", "Here: Zander, Carp and Catfish get dropped. Everything else is kept."),
        "drop": ("Drop confirm", "To drop fish the bot needs the game's Yes button. Drop one fish by hand once..."),
        "drop_confirm": ("Drop confirm", "...press Confirm in the bot, then click Yes in the game."),
        "start": ("Start", "Everything is set: START."),
        "f5": ("F5", "F5 pauses every bot at once. F5 again and they carry on."),
    },
    "jigsaw": {
        "mode": ("Jigsaw puzzle solver", "Same windows again. Switch the mode at the top."),
        "grid": ("Puzzle window", "The bot finds the puzzle window by itself, in every client."),
        "placement": ("Keep it off the middle", "The game's Yes/No dialog pops up in the middle of the screen. Don't put the puzzle window under it: top left works."),
        "confirm": ("Set Confirm", "Open one crate by hand, press Set Confirm, then click Yes in the game."),
        "start": ("Start", "And START."),
    },
}
CARDS = {
    "classic": ("1  ·  Classic fishing", "for servers without the fishing minigame"),
    "minigame": ("2  ·  The fishing minigame", "the new fishing: click the fish"),
    "jigsaw": ("3  ·  Jigsaw puzzle solver", "opens the crates, places every piece"),
}
COUNTERS = {"classic": [("b", "bites {}")], "minigame": [("b", "games {}")],
            "jigsaw": [("a", "crates {}"), ("b", "boards {}")]}
FIRST_SCENE = {"classic": "part_classic", "minigame": "part_minigame_shown", "jigsaw": "part_jigsaw_shown"}
LIVE = 12.0          # seconds of 4 clients at real speed after START
TL_MIN = 16.0        # shortest timelapse


def run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"failed: {' '.join(map(str, cmd))[:600]}\n{r.stderr[-3000:]}")
    return r


def enc(out):
    return ["-an", "-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "19",
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)]


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).stdout)


class Take:
    def __init__(self, part, name):
        self.part = part
        self.dir = RAW / part / name
        self.desk = self.dir / "desktop.mkv"
        self.desk0 = float((self.dir / "desktop.mkv.log").read_text().split()[0])
        self.ev = []
        for line in (self.dir / "events.log").read_text().splitlines():
            p = line.split()
            if len(p) >= 2:
                self.ev.append((float(p[0]), p[1], p[2:]))
        self.stats = list(csv.DictReader(open(self.dir / "stats.csv")))

    def scene(self, name):
        for t, k, a in self.ev:
            if k == "scene" and a and a[0] == name:
                return t
        raise KeyError(f"{self.part}: no scene {name}")

    def has(self, name):
        return any(k == "scene" and a and a[0] == name for _, k, a in self.ev)

    def events(self, kind, t0, t1):
        return [(t, a) for t, k, a in self.ev if k == kind and t0 <= t <= t1]

    def counter(self, col):
        out = []
        for r in self.stats:
            tot = 0
            for i in range(1, 5):
                m = re.search(r"(\d[\d,]*)", r.get(f"w{i}_{col}", "") or "")
                tot += int(m.group(1).replace(",", "")) if m else 0
            out.append((float(r["t"]), tot))
        return out

    TROUBLE = ("No bite on this bait key", "Stopping bot", "inventory is gone", "could not be sent",
               "no minigame seen")

    def run_end(self, run0):
        end = self.scene("stop") - 1.0
        for r in self.stats:                  # stop before the first sign of trouble in any window
            t = float(r["t"])
            if t > run0 + 1 and any(k in (r.get(f"w{i}_msg") or "") for i in range(1, 5) for k in self.TROUBLE):
                end = min(end, t - 4.0)
                break
        if self.part == "jigsaw":           # stop before a window paused by itself
            for r in self.stats:
                t = float(r["t"])
                if t > run0 + 1 and any("PAUSED" in (r.get(f"w{i}_pill") or "")
                                        and "Stopped" not in (r.get(f"w{i}_msg") or "") for i in range(1, 5)):
                    return min(end, t - 1.5)
        return end


# ---------------------------------------------------------------- video segments
SEGS = []


def seg(label, dur, args, vf):
    out = WORK / f"seg{len(SEGS):02d}.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, "-vf", vf, "-t", f"{dur:.3f}", *enc(out)])
    SEGS.append((out, dur, label))
    print(f"  seg {len(SEGS) - 1:02d} {dur:6.2f}s {label}", flush=True)


def desk_seg(tk, t0, dur, label, speed=1.0):
    a = t0 - tk.desk0
    seg(label, dur, ["-i", tk.desk],
        f"trim=start={a:.3f}:duration={dur * speed:.3f},setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS},"
        f"scale={W}:{H}")


def card_seg(tk, t, dur, label):
    """Title card: the next part's first frame, blurred and darkened."""
    a = t - tk.desk0
    seg(label, dur, ["-ss", f"{a:.3f}", "-i", tk.desk],
        f"trim=end_frame=1,loop=loop=-1:size=1,setpts=N/{FPS}/TB,fps={FPS},scale={W}:{H},"
        f"boxblur=24:2,eq=brightness=-0.18:saturation=0.8,fade=in:st=0:d=0.35")


# ---------------------------------------------------------------- text (ASS)
def ts(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


class Ass:
    def __init__(self):
        self.lines = []

    def text(self, t0, t1, txt, style, fade=(200, 200), extra="", layer=2):
        self.lines.append(f"Dialogue: {layer},{ts(t0)},{ts(t1)},{style},,0,0,0,,{{\\fad({fade[0]},{fade[1]}){extra}}}{txt}")

    def rect(self, t0, t1, x, y, w, h, outline=True, color=ACCENT, alpha="00", r=10, layer=1, fade=(150, 250)):
        x0, y0, x1, y1 = int(x), int(y), int(x + w), int(y + h)
        r = int(min(r, w / 2, h / 2))
        path = (f"m {x0 + r} {y0} l {x1 - r} {y0} b {x1} {y0} {x1} {y0} {x1} {y0 + r} l {x1} {y1 - r} "
                f"b {x1} {y1} {x1} {y1} {x1 - r} {y1} l {x0 + r} {y1} b {x0} {y1} {x0} {y1} {x0} {y1 - r} "
                f"l {x0} {y0 + r} b {x0} {y0} {x0} {y0} {x0 + r} {y0}")
        if outline:
            tag = f"\\an7\\pos(0,0)\\bord5\\shad0\\1a&HFF&\\3c{color}\\3a&H{alpha}&"
        else:
            tag = f"\\an7\\pos(0,0)\\bord0\\shad0\\1c&H161412&\\1a&H{alpha}&"
        self.lines.append(f"Dialogue: {layer},{ts(t0)},{ts(t1)},Draw,,0,0,0,,{{\\fad({fade[0]},{fade[1]}){tag}\\p1}}{path}")

    def write(self, path):
        px, py, pw, ph = PANEL
        ml = px + 34
        mr = W - (px + pw) + 30
        head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Draw,Inter,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Tag,Inter SemiBold,34,{ACCENT},{ACCENT},&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,0,0,7,{ml},{mr},{py + 30},1
Style: Title,Inter SemiBold,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,2,7,{ml},{mr},{py + 92},1
Style: Body,Inter,46,&H00E6E6E6,&H00E6E6E6,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,2,7,{ml},{mr},{py + 186},1
Style: Count,JetBrains Mono Medium,58,&H00BFE3FF,&H00BFE3FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,2,1,{ml},{mr},40,1
Style: Big,Inter SemiBold,128,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,0,4,5,80,80,0,1
Style: Sub,Inter,58,&H00E0E0E0,&H00E0E0E0,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,0,3,5,80,80,0,1
Style: Link,JetBrains Mono Medium,60,&H00BFE3FF,&H00BFE3FF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,0,3,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        Path(path).write_text(head + "\n".join(self.lines) + "\n", encoding="utf-8")


def counters(ass, tk, col, fmt, t0, t1, out0, speed, line):
    pts = [(t, v) for t, v in tk.counter(col) if t0 <= t <= t1]
    if not pts:
        return 0
    base = pts[0][1]
    marks, shown = [], None
    for t, v in pts:
        if v != shown:
            marks.append((t, v))
            shown = v
    y = PANEL[1] + PANEL[3] - 40 - 80 * line
    for i, (t, v) in enumerate(marks):
        a = out0 + (t - t0) / speed
        b = out0 + ((marks[i + 1][0] if i + 1 < len(marks) else t1) - t0) / speed
        ass.text(a, b, fmt.format(v - base), "Count", fade=(0, 0), extra=f"\\an1\\pos({PANEL[0] + 34},{y})")
    return marks[-1][1] - base


# ---------------------------------------------------------------- one part
def part(ass, tk, t, tl_target):
    p0 = t
    first = tk.scene(FIRST_SCENE[tk.part])
    title, sub = CARDS[tk.part]
    d = BAR                                  # title card: 1 bar
    card_seg(tk, first + 0.2, d, f"{tk.part} card")
    ass.text(t + 0.2, t + d - 0.15, title, "Big", fade=(300, 200), extra="\\pos(1280,660)")
    ass.text(t + 0.5, t + d - 0.15, sub, "Sub", fade=(300, 200), extra="\\pos(1280,800)")
    t += d

    run0 = tk.scene("running")
    s0, s1 = first - 0.3, run0 + 0.3
    setup = s1 - s0
    seg_t0 = t
    desk_seg(tk, s0, setup, f"{tk.part} setup")
    says = tk.events("say", s0, s1)
    for i, (st, a) in enumerate(says):
        tt = TEXT[tk.part].get(a[0])
        if not tt:
            continue
        a0 = seg_t0 + (st - s0)
        a1 = seg_t0 + ((says[i + 1][0] if i + 1 < len(says) else s1) - s0)
        prev = TEXT[tk.part].get(says[i - 1][1][0], ("",)) if i else ("",)
        ass.text(a0, a1, tt[0], "Title", fade=(0 if prev[0] == tt[0] else 200, 0))
        ass.text(a0, a1, tt[1], "Body", fade=(200, 0))
    for ht, a in tk.events("hl", s0, s1):
        x, y, w_, h_ = (int(v) for v in a[:4])
        a0 = seg_t0 + (ht - s0)
        ass.rect(a0, a0 + 2.6, x - 8, y - 8, w_ + 16, h_ + 16)
    t += setup

    desk_seg(tk, run0 + 0.3, LIVE, f"{tk.part} live")       # 4 clients at real speed
    ass.text(t, t + LIVE, "Running", "Title")
    ass.text(t, t + LIVE, "4 clients at real speed. The bot only reads the screen.", "Body")
    t += LIVE
    after = run0 + 0.3 + LIVE

    if tk.part == "minigame" and tk.has("f5_pause"):
        f0 = tk.scene("f5_pause")
        f1 = tk.scene("f5_resume") if tk.has("f5_resume") else f0 + 4
        d = (f1 - f0) + 3.5
        desk_seg(tk, f0 - 1.0, d, "f5")
        ass.text(t, t + d, TEXT["minigame"]["f5"][0], "Title")
        ass.text(t, t + d, TEXT["minigame"]["f5"][1], "Body")
        t += d
        after = f1 + 2.5

    run1 = tk.run_end(run0)                  # the timelapse fills the part up to whole bars
    used = t - p0
    bars = -(-(used + tl_target) // BAR)
    tl = max(TL_MIN, bars * BAR - used)
    if bars * BAR - used < TL_MIN:
        tl = (bars + 1) * BAR - used
    speed = (run1 - after) / tl
    desk_seg(tk, after, tl, f"{tk.part} timelapse", speed=speed)
    ass.text(t, t + tl, f"Timelapse  ×{speed:.0f}", "Title")
    ass.text(t, t + tl, "Same 4 clients, sped up.", "Body")
    nc = len(COUNTERS[tk.part])
    for line, (col, fmt) in enumerate(COUNTERS[tk.part]):
        n = counters(ass, tk, col, fmt, after, run1, t, speed, nc - 1 - line)
        print(f"  {tk.part}: {fmt.format(n)} in the timelapse")
    t += tl

    px, py, pw, ph = PANEL                   # panel + part tag for the whole part (not the card)
    ass.rect(p0 + BAR, t, px, py, pw, ph, outline=False, alpha="38", r=18, layer=0, fade=(250, 250))
    ass.text(p0 + BAR, t, title.replace("  ·  ", " · "), "Tag", fade=(250, 250))
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", default="v2")
    ap.add_argument("--minigame-take", default="v7")
    ap.add_argument("--out", default=str(Path.home() / "Videos/Fishing-Puzzle-Player-trailer-v2.0.5.mp4"))
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    ass = Ass()
    t = 0.0
    marks = {}

    d = 3 * BAR                               # intro: 3 bars, still camera
    seg("intro", d, ["-i", RAW / "intro1440.mp4"],
        f"trim=start=0.5:duration={d:.3f},setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H},fade=in:st=0:d=1.2")
    ass.text(0.9, d - 0.2, "Fishing Puzzle Player", "Big", fade=(500, 300), extra="\\pos(1280,1040)")
    ass.text(1.6, d - 0.2, "free Metin2 fishing bot  ·  v2.0.5", "Sub", fade=(500, 300), extra="\\pos(1280,1180)")
    t += d

    for name, target in (("classic", 18), ("minigame", 20), ("jigsaw", 22)):
        marks[name] = round(t / BAR)
        t = part(ass, Take(name, a.minigame_take if name == "minigame" else a.take), t, target)

    marks["outro"] = round(t / BAR)           # end: the dance, blurred, under the links: 4 bars
    d = 4 * BAR
    seg("end", d, ["-i", RAW / "dance1440.mp4"],
        f"trim=start=1:duration={d:.3f},setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H},"
        f"boxblur=18:2,eq=brightness=-0.12:saturation=0.9,fade=in:st=0:d=0.4")
    ass.text(t + 0.3, t + d, "Download (free)", "Big", fade=(300, 0), extra="\\fs104\\pos(1280,430)")
    ass.text(t + 0.6, t + d, "github.com/hetzpvp/Mt2-Fishbot/releases", "Link", fade=(300, 0), extra="\\pos(1280,560)")
    ass.text(t + 0.9, t + d, "Windows  ·  Linux", "Sub", fade=(300, 0), extra="\\fs46\\pos(1280,650)")
    ass.text(t + 1.4, t + d, "Questions or issues? Join our Discord", "Big", fade=(300, 0), extra="\\fs84\\pos(1280,880)")
    ass.text(t + 1.7, t + d, "discord.gg/vZaHCRFqW7", "Link", fade=(300, 0), extra="\\pos(1280,1000)")
    t += d
    bars = round(t / BAR)
    print(f"total {t:.2f} s = {t / BAR:.2f} bars; {marks}")

    secs = f"0:intro,{marks['classic']}:a,{marks['minigame']}:b,{marks['jigsaw']}:c,{marks['outro']}:outro"
    mus, _ = music.render(WORK / "music.wav", bars, music.parse_sections(secs, bars))

    lst = WORK / "segments.txt"
    lst.write_text("".join(f"file '{p}'\n" for p, _, _ in SEGS))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", joined])
    assf = WORK / "text.ass"
    ass.write(assf)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", joined, "-i", mus,
         "-filter_complex", f"[0:v]ass={assf}:fontsdir=/usr/share/fonts,fade=out:st={t - 1.0:.2f}:d=1.0[v];"
                            f"[1:a]atrim=0:{t:.3f},afade=out:st={t - 3:.2f}:d=3[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr",
         "-cq", "20", "-b:v", "0", "-maxrate", "60M", "-bufsize", "120M", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-t", f"{t:.3f}", out])
    print(f"wrote {out} ({duration(out):.2f} s)")
    json.dump({"segments": [(str(p), d, l) for p, d, l in SEGS], "total": t, "sections": secs},
              open(WORK / "timeline.json", "w"), indent=1)


if __name__ == "__main__":
    main()
