"""
Records what a real client shows between cast and reel-in (not a test):

    .venv/bin/python tools/realclient/record_bite.py classic|fish [seconds]

One hidden client casts once (bait key 1 + space) and the window is captured
every 100 ms; frames go to ~/.local/share/fishbot-realclient/explore/<stamp>-bite/
together with a contact sheet, plus the server-side state at the end.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "src"))

from display import HiddenDisplay, assert_hidden  # noqa: E402
from rig import RigSession  # noqa: E402


def main():
    instance = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 45
    out = os.path.expanduser(f"~/.local/share/fishbot-realclient/explore/{time.strftime('%Y%m%d_%H%M%S')}-bite-{instance}")
    os.makedirs(out, exist_ok=True)
    import cv2
    import numpy as np
    with HiddenDisplay() as disp:
        disp.activate()
        rig = RigSession(instance, disp)
        rig.up({"restart_on_crash": 0, "clients": ["admin"],
                **({"server": {"fish_event": False}} if instance == "fish" else {})})
        assert_hidden()
        from platforms import x11
        from pynput.keyboard import Controller, Key
        c = rig.clients["admin"]
        if "closemap" in sys.argv:
            print("area map closed:", rig.close_area_map("admin"))
        x11.set_foreground(c.window)
        time.sleep(0.5)
        k = Controller()
        casts = int(os.environ.get("CASTS", "1"))
        frames, prev, n = [], None, 0
        t0 = time.time()
        for cast in range(casts):
            x11.set_foreground(c.window)
            time.sleep(0.3)
            for key in ("1", Key.space):
                k.press(key)
                time.sleep(0.04)
                k.release(key)
                time.sleep(0.08)
            tc = time.time()
            with open(os.path.join(out, "casts.txt"), "a") as fh:
                fh.write(f"{tc - t0:.1f}\n")
            while time.time() - tc < secs:
                img = x11.render_window(c.window, 800, 600)
                if img is not None:
                    bgr = np.ascontiguousarray(img[:, :, :3])
                    top = bgr[:300, 275:525]
                    if prev is None or np.abs(top.astype(np.int16) - prev).max(axis=2).mean() > 2.0 or n % 20 == 0:
                        cv2.imwrite(os.path.join(out, f"f{n:04d}_{time.time() - t0:05.1f}s.png"), bgr)
                        frames.append(top)
                    prev = top.astype(np.int16)
                n += 1
                time.sleep(0.1)
        print("frames kept:", len(frames), "dir", out)
        # the bot's classic bite template, matched like wait_for_classic_fish does
        tpl = cv2.cvtColor(cv2.imread(os.path.join(os.path.dirname(os.path.dirname(HERE)), "assets", "fishing",
                                                   "classic_fish.jpg")), cv2.COLOR_BGR2GRAY)
        best = []
        for f in sorted(os.listdir(out)):
            if not f.startswith("f"):
                continue
            g = cv2.cvtColor(cv2.imread(os.path.join(out, f))[:300, 275:525], cv2.COLOR_BGR2GRAY)
            m = 0.0
            for s in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4):
                t = cv2.resize(tpl, (int(tpl.shape[1] * s), int(tpl.shape[0] * s)))
                m = max(m, float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED).max()))
            best.append((m, f))
        best.sort(reverse=True)
        print("best template scores:", [(round(m, 3), f) for m, f in best[:6]])
        final = rig.finish()
        print(final.get("admin", {}).get("totals"))
        rig.close()


if __name__ == "__main__":
    main()
