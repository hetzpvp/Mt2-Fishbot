#!/usr/bin/env python3
"""One trailer take, start to finish: clients up, recorders on, GUI driven, checks.

    python3 tools/trailer/takes.py PART [--run SECONDS] [--name take1] [--no-screencast]

PART: classic | minigame | jigsaw. Footage goes to
metin2-local-server/modding/out/video/fishbot/raw/<PART>/<name>/:
  desktop.mkv (+.log)      the whole desktop with the cursor (portal)
  w1..w4.mkv (+.log)       each client, x11grab (from the moment the bots run)
  events.log, stats.csv    from drive_gui.py
  up.json, inspect.json    the rig's view before / after
  report.json              the checks
Moves the real cursor: only while nobody uses the PC.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT = HERE.parents[1]
SERVER = Path.home() / "Documents/Github/metin2-local-server"
FISH = SERVER / "modding/out/video/fishbot"
SCEN = FISH / "scenarios"
TESTRIG = ["python3", str(SERVER / "scripts/testrig.py")]
INSTANCE = {"classic": "classic", "minigame": "fish", "jigsaw": "fish"}
RUN = {"classic": 300, "minigame": 300, "jigsaw": 150}
WORM, BOX, SPECIAL = "27801", "25106", "25107"
LAYOUT = [(820, 40), (10, 40), (10, 700), (820, 700)]
GUI_POS = "1632,60"


def log(msg):
    print(f"[take] {msg}", flush=True)


def rig(*args, timeout=600):
    r = subprocess.run(TESTRIG + list(args), capture_output=True, text=True, timeout=timeout, cwd=SERVER,
                       env=dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0")))
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise SystemExit(f"testrig {' '.join(args)} failed:\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}")


def x11grab(xid, out):
    logf = open(str(out) + ".log", "w")
    logf.write(f"{time.time():.3f} capture_start\n")
    logf.close()
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-thread_queue_size", "512",
         "-f", "x11grab", "-draw_mouse", "0", "-framerate", "30", "-window_id", str(xid), "-i", os.environ.get("DISPLAY", ":0"),
         "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.DEVNULL)


def stop(p, sig=signal.SIGINT, wait=15):
    if p and p.poll() is None:
        p.send_signal(sig)
        try:
            p.wait(wait)
        except subprocess.TimeoutExpired:
            p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=list(INSTANCE))
    ap.add_argument("--run", type=float)
    ap.add_argument("--name", default=time.strftime("%H%M%S"))
    ap.add_argument("--no-screencast", action="store_true")
    a = ap.parse_args()
    inst = INSTANCE[a.part]
    out = FISH / "raw" / a.part / a.name
    out.mkdir(parents=True, exist_ok=True)
    report = {"part": a.part, "take": a.name, "ok": False, "problems": []}

    log(f"up trailer-{a.part} ({inst})")
    up = rig("up", str(SCEN / f"trailer-{a.part}.json"), timeout=900)
    (out / "up.json").write_text(json.dumps(up, indent=1))
    if not up.get("ok"):
        raise SystemExit(f"up failed: {up.get('error')}")
    clients = up["clients"]
    xids = [c["window"] for c in clients]
    # our own layout on the 2560x1440 monitor: W01 top right, next to the bot window (x >= 1632)
    for xid, (x, y) in zip(xids, LAYOUT):
        subprocess.run(["xdotool", "windowmove", str(xid), str(x), str(y)])
    time.sleep(1.0)
    for c in clients:
        r = c.get("ready") or {}
        if r.get("area_map_open"):
            report["problems"].append(f"{c['account']}: area map open")
        if not r.get("inventory_open"):
            report["problems"].append(f"{c['account']}: inventory closed")
        if a.part == "jigsaw":
            jw = r.get("jigsaw_window") or {}
            if r.get("jigsaw") != "puzzle" or jw.get("x") != 10 or jw.get("y") != 10:
                report["problems"].append(f"{c['account']}: puzzle not open at 10,10 ({r.get('jigsaw')}, {jw})")
    if report["problems"]:
        rig("ui", "--instance", inst, "--area-map", "closed", "--inventory", "open")
        log(f"fixed up UI: {report['problems']}")
        report["ui_fixed"], report["problems"] = report["problems"], []
    time.sleep(2)

    sc = None
    if not a.no_screencast:
        sc = subprocess.Popen(["python3", str(HERE / "screencast.py"), str(out / "desktop.mkv")])
        for _ in range(100):
            if (out / "desktop.mkv.log").exists():
                break
            time.sleep(0.1)
        else:
            stop(sc)
            raise SystemExit("screencast did not start (portal dialog not approved?)")

    run = a.run or RUN[a.part]
    drv = subprocess.Popen([str(BOT / ".venv/bin/python"), str(HERE / "drive_gui.py"), a.part,
                            "--windows", ",".join(str(x) for x in xids), "--out", str(out), "--run", str(run),
                            "--gui-pos", GUI_POS],
                           cwd=BOT, env=dict(os.environ, QT_SCALE_FACTOR="1"))
    grabs = []
    events = out / "events.log"
    try:
        while drv.poll() is None:
            time.sleep(0.3)
            if not grabs and events.exists() and "scene running" in events.read_text():
                grabs = [x11grab(x, out / f"w{i + 1}.mkv") for i, x in enumerate(xids)]
                log("bots running: per-window capture on")
        code = drv.returncode
    finally:
        stop(drv, signal.SIGTERM, 20)
        for g in grabs:
            stop(g)
        stop(sc, wait=30)
    report["drive_exit"] = code
    if code:
        report["problems"].append(f"drive_gui exit {code}")
    dead = [i + 1 for i, g in enumerate(grabs) if g.returncode not in (0, 255, -2)]
    if dead:
        report["problems"].append(f"x11grab of window(s) {dead} ended with an error")

    log("down (exact inventory)")
    down = rig("down", "--instance", inst, "--keep-vm", "--inspect", timeout=600)
    (out / "inspect.json").write_text(json.dumps(down, indent=1))
    chars = {c["account"]: c for c in (down.get("inspect") or {}).get("characters", [])}
    summary = {}
    mine = {c["account"] for c in clients}
    for acc, c in chars.items():
        if acc not in mine:
            continue
        tot = c.get("totals", {})
        worms = sum(v for k, v in tot.items() if k == WORM)
        others = {k: v for k, v in tot.items() if k not in (WORM, BOX, SPECIAL)}
        summary[acc] = {"worm_used": 800 - worms, "items_caught": sum(others.values()),
                        "boxes_used": 200 - tot.get(BOX, 0) if a.part == "jigsaw" else None,
                        "jigsaw": c.get("jigsaw")}
    report["inspect"] = summary
    if a.part in ("classic", "minigame"):
        for acc, s in summary.items():
            if s["items_caught"] < (3 if a.part == "classic" else 10):
                report["problems"].append(f"{acc}: only {s['items_caught']} catches in the inventory")
    else:
        for acc, s in summary.items():
            if (s["boxes_used"] or 0) < 20:
                report["problems"].append(f"{acc}: only {s['boxes_used']} boxes used")
    report["ok"] = not report["problems"] and code == 0
    (out / "report.json").write_text(json.dumps(report, indent=1))
    log(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
