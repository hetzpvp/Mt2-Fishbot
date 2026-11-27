#!/usr/bin/env python3
"""Full-desktop recording with the cursor on KDE Wayland, through the screencast portal.

    python3 tools/trailer/screencast.py OUT.mkv [--token FILE] [--fps 60]

The first run shows KDE's "share your screen" dialog; the approval is kept
(persist_mode 2, restore token in --token), so later runs start without it.
Stop with SIGINT/SIGTERM: the file is finished cleanly (EOS). OUT.log gets
"<wall time> capture_start" at the first frame, like the other recorders.
"""
from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--token", default=os.path.expanduser("~/.cache/fishbot-trailer-portal-token"))
ap.add_argument("--fps", type=int, default=60)
ap.add_argument("--seconds", type=float, default=0, help="stop by itself after this long (0 = until signalled)")
A = ap.parse_args()

Gst.init(None)
loop = GLib.MainLoop()
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
sender = bus.get_unique_name()[1:].replace(".", "_")
PORTAL, PATH = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.ScreenCast"
state = {"session": None, "pipeline": None, "started": False}


def fail(msg):
    print(f"screencast: {msg}", file=sys.stderr)
    os._exit(1)


def request(method, args, on_done):
    token = f"fpp{random.randrange(1 << 30)}"
    path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def on_response(_c, _s, _p, _i, _sig, params, _u):
        code, results = params.unpack()
        bus.signal_unsubscribe(sub)
        if code != 0:
            fail(f"{method} refused or cancelled (response {code})")
        on_done(results)
    sub = bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
                               Gio.DBusSignalFlags.NO_MATCH_RULE, on_response, None)
    opts = args[-1]
    opts["handle_token"] = GLib.Variant("s", token)
    sig = {"CreateSession": "(a{sv})", "SelectSources": "(oa{sv})", "Start": "(osa{sv})"}[method]
    bus.call_sync(PORTAL, PATH, IFACE, method, GLib.Variant(sig, tuple(args)), None,
                  Gio.DBusCallFlags.NONE, -1, None)


def create():
    request("CreateSession", [{"session_handle_token": GLib.Variant("s", "fppsession")}], select)


def select(results):
    state["session"] = results["session_handle"]
    opts = {"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", 2), "persist_mode": GLib.Variant("u", 2)}
    if os.path.exists(A.token):
        tok = open(A.token).read().strip()
        if tok:
            opts["restore_token"] = GLib.Variant("s", tok)
    request("SelectSources", [state["session"], opts], start)


def start(_results):
    request("Start", [state["session"], "", {}], started)


def started(results):
    if "restore_token" in results:
        os.makedirs(os.path.dirname(A.token), exist_ok=True)
        with open(A.token, "w") as f:
            f.write(results["restore_token"])
    streams = results.get("streams") or []
    if not streams:
        fail("no stream")
    node, props = streams[0]
    print(f"screencast: node {node} size {props.get('size')}", file=sys.stderr)
    reply, fds = bus.call_with_unix_fd_list_sync(
        PORTAL, PATH, IFACE, "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (state["session"], {})),
        GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None)
    fd = fds.get(reply.unpack()[0])
    desc = (f"pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=500 always-copy=true ! "
            f"queue max-size-buffers=8 ! videoconvert ! videorate ! video/x-raw,format=NV12,framerate={A.fps}/1 ! "
            f"queue ! nvautogpuh264enc rc-mode=vbr const-quality=19 bitrate=0 max-bitrate=60000 gop-size={A.fps * 2} ! "
            f"h264parse ! matroskamux ! filesink location={A.out}")
    p = Gst.parse_launch(desc)
    state["pipeline"] = p
    src = p.get_by_name("pipewiresrc0") or next(e for e in p.iterate_elements() if "pipewiresrc" in e.get_name())

    def first_buffer(_pad, _info):
        if not state["started"]:
            state["started"] = True
            with open(A.out + ".log", "w") as f:
                f.write(f"{time.time():.3f} capture_start\n")
            print("screencast: recording", file=sys.stderr, flush=True)
        return Gst.PadProbeReturn.REMOVE
    src.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, first_buffer)
    gbus = p.get_bus()
    gbus.add_signal_watch()
    gbus.connect("message::eos", lambda *_: finish())
    gbus.connect("message::error", lambda _b, m: fail(f"gst: {m.parse_error()}"))
    p.set_state(Gst.State.PLAYING)
    if A.seconds:
        GLib.timeout_add(int(A.seconds * 1000), stop)


def stop(*_):
    p = state["pipeline"]
    if p is None:
        finish()
        return False
    p.send_event(Gst.Event.new_eos())
    GLib.timeout_add(8000, finish)      # EOS lost: close anyway
    return False


def finish(*_):
    p = state["pipeline"]
    if p is not None:
        p.set_state(Gst.State.NULL)
    if state["session"]:
        try:
            bus.call_sync(PORTAL, state["session"], "org.freedesktop.portal.Session", "Close", None, None,
                          Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass
    loop.quit()
    return False


GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, stop)
GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, stop)
create()
loop.run()
print(f"screencast: wrote {A.out}", file=sys.stderr)
