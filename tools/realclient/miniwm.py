"""
A minimal EWMH window manager for a private X display (Xvfb), in Python.

The real-client tests run the game clients on an invisible Xvfb display so
they never touch the user's screen, mouse or keyboard. The bot needs a window
manager there for the same things it uses on a desktop:

- ``_NET_ACTIVE_WINDOW`` requests (focus switch) and the root property that
  says which window is active
- ``_NET_CLIENT_LIST`` (window list) and ``_NET_SUPPORTING_WM_CHECK``
- iconify through ``WM_CHANGE_STATE`` (``WM_STATE`` Iconic +
  ``_NET_WM_STATE_HIDDEN``), and restore through ``_NET_ACTIVE_WINDOW``
- click to focus, like KWin: a click into an inactive window activates it
  and still reaches the window
- move / resize requests (``ConfigureRequest``), honoured as asked

By default no decorations and no reparenting: the client area is the X
window, which is what the bot reads on KWin too. Openbox would do as well;
this one needs only python-xlib, which the bot already depends on.

Two switches make the display look like other desktops
(docs/TESTRIG_SCENARIOS.md, R18):

- ``--frame TOP,BORDER``: every window gets a frame (reparented) with a
  TOP-px title bar and BORDER-px borders, like KWin or Windows; the frame
  extents are published as ``_NET_FRAME_EXTENTS``. Move requests place the
  client area where asked (the frame goes round it).
- ``--composite on|off`` (default on): Composite redirection of every
  top-level window, as on Xwayland and composited KWin: a covered or
  partly offscreen window still has all its own pixels. With ``off`` the
  display behaves like X11 without a compositor: covered parts read as
  black and a capture that reaches past the screen edge fails (BadMatch).

    python tools/realclient/miniwm.py [--display :N] [--frame 31,8] [--composite off]

Runs until the X server goes away or it gets SIGTERM.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from Xlib import X, Xatom, display, error
from Xlib.protocol import event

NORMAL, ICONIC, WITHDRAWN = 1, 3, 0


class MiniWM:
    def __init__(self, name: str | None = None, frame: tuple[int, int] | None = None,
                 composite: bool = True):
        self.d = display.Display(name)
        self.screen = self.d.screen()
        self.root = self.screen.root
        self.frame_top, self.frame_border = frame or (0, 0)
        self.frames: dict[int, object] = {}   # client id -> frame window (with --frame)
        self.composite = composite
        self.clients: list[int] = []       # managed windows, in map order
        self.iconic: set[int] = set()
        self.active = 0
        self._expect_unmap: set[int] = set()
        self.atom = lambda n: self.d.intern_atom(n)
        self._become_wm()

    # ------------------------------------------------------------- setup
    def _become_wm(self) -> None:
        caught = []
        self.root.change_attributes(
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask | X.PropertyChangeMask,
            onerror=lambda *a: caught.append(a))
        self.d.sync()
        if caught:
            raise SystemExit("another window manager is running on this display")
        if self.composite:
            try:
                from Xlib.ext import composite as xcomposite
                xcomposite.redirect_subwindows(self.root, xcomposite.RedirectAutomatic)
            except Exception as e:      # no Composite extension: run like plain X11
                print(f"miniwm: no composite redirection ({e})", file=sys.stderr)
                self.composite = False
        check = self.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent)
        for w in (self.root, check):
            w.change_property(self.atom("_NET_SUPPORTING_WM_CHECK"), Xatom.WINDOW, 32, [check.id])
        check.change_property(self.atom("_NET_WM_NAME"), self.atom("UTF8_STRING"), 8, b"fishbot-miniwm")
        supported = ["_NET_ACTIVE_WINDOW", "_NET_CLIENT_LIST", "_NET_CLIENT_LIST_STACKING",
                     "_NET_SUPPORTING_WM_CHECK", "_NET_WM_STATE", "_NET_WM_STATE_HIDDEN",
                     "_NET_WM_NAME", "_NET_WM_PID", "_NET_FRAME_EXTENTS"]
        self.root.change_property(self.atom("_NET_SUPPORTED"), Xatom.ATOM, 32,
                                  [self.atom(a) for a in supported])
        for w in self.root.query_tree().children:   # windows mapped before we started
            try:
                a = w.get_attributes()
                if a.map_state == X.IsViewable and not a.override_redirect:
                    self._manage(w.id)
            except error.XError:
                pass
        self._publish()
        self.d.flush()

    # ------------------------------------------------------------- state
    def _win(self, wid):
        return self.d.create_resource_object("window", wid)

    # ------------------------------------------------------------- frames
    def _top(self, wid):
        """The window that sits in the root for client ``wid``: its frame, or itself."""
        return self.frames.get(wid) or self._win(wid)

    def _reparent(self, wid: int) -> None:
        """Put the client into a frame (only with --frame)."""
        if not (self.frame_top or self.frame_border) or wid in self.frames:
            return
        w = self._win(wid)
        g = w.get_geometry()
        t, b = self.frame_top, self.frame_border
        f = self.root.create_window(g.x - b, g.y - t, g.width + 2 * b, g.height + t + b, 0,
                                    self.screen.root_depth, X.InputOutput, X.CopyFromParent,
                                    background_pixel=0x3C3F41,
                                    event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self._expect_unmap.add(wid)       # reparenting a mapped window unmaps it once
        w.change_save_set(X.SetModeInsert)
        w.reparent(f, b, t)
        w.change_property(self.atom("_NET_FRAME_EXTENTS"), Xatom.CARDINAL, 32, [b, b, t, b])
        f.map()
        self.frames[wid] = f

    def _unframe(self, wid: int) -> None:
        """Take the frame away. A client that still exists goes back to the
        root first: destroying the frame would destroy it too."""
        f = self.frames.pop(wid, None)
        if f is None:
            return
        try:
            w = self._win(wid)
            o = w.translate_coords(self.root, 0, 0)
            w.reparent(self.root, -o.x, -o.y)
        except error.XError:
            pass                        # already destroyed
        try:
            f.destroy()
        except error.XError:
            pass

    def _send_configure_notify(self, wid: int) -> None:
        """ICCCM 4.1.5: a reparented client learns its root position from a
        synthetic ConfigureNotify."""
        w = self._win(wid)
        try:
            g = w.get_geometry()
            o = w.translate_coords(self.root, 0, 0)
            ev = event.ConfigureNotify(event=w, window=w, above_sibling=X.NONE, x=-o.x, y=-o.y,
                                       width=g.width, height=g.height, border_width=0,
                                       override=False)
            w.send_event(ev, event_mask=X.StructureNotifyMask)
        except error.XError:
            pass

    def _publish(self) -> None:
        live = [c for c in self.clients]
        self.root.change_property(self.atom("_NET_CLIENT_LIST"), Xatom.WINDOW, 32, live)
        self.root.change_property(self.atom("_NET_CLIENT_LIST_STACKING"), Xatom.WINDOW, 32, live)
        self.root.change_property(self.atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW, 32, [self.active])

    def _set_state(self, wid: int, state: int) -> None:
        w = self._win(wid)
        try:
            w.change_property(self.atom("WM_STATE"), self.atom("WM_STATE"), 32, [state, 0])
            hidden = [self.atom("_NET_WM_STATE_HIDDEN")] if state == ICONIC else []
            w.change_property(self.atom("_NET_WM_STATE"), Xatom.ATOM, 32, hidden)
        except error.XError:
            pass

    def _manage(self, wid: int) -> None:
        if wid not in self.clients:
            self.clients.append(wid)
            try:
                self._win(wid).change_attributes(event_mask=X.StructureNotifyMask | X.FocusChangeMask)
                self._reparent(wid)
            except error.XError:
                pass
        self.iconic.discard(wid)
        self._set_state(wid, NORMAL)

    def _forget(self, wid: int) -> None:
        self._unframe(wid)
        if wid in self.clients:
            self.clients.remove(wid)
        self.iconic.discard(wid)
        if self.active == wid:
            self.active = 0
            # like a desktop WM: the next window gets the focus
            for c in reversed(self.clients):
                if c not in self.iconic:
                    self._activate(c)
                    return
        self._publish()

    def _grab_click(self, wid: int, grab: bool) -> None:
        """Inactive windows get a passive button grab: the first click
        activates them and is then replayed to the window (click to focus)."""
        w = self._win(wid)
        try:
            if grab:
                w.grab_button(X.AnyButton, X.AnyModifier, False, X.ButtonPressMask,
                              X.GrabModeSync, X.GrabModeAsync, X.NONE, X.NONE)
            else:
                w.ungrab_button(X.AnyButton, X.AnyModifier)
        except error.XError:
            pass

    def _activate(self, wid: int) -> None:
        w = self._win(wid)
        try:
            if wid in self.iconic:
                self.iconic.discard(wid)
                w.map()
                if wid in self.frames:
                    self.frames[wid].map()
                self._set_state(wid, NORMAL)
            self._top(wid).configure(stack_mode=X.Above)
            w.set_input_focus(X.RevertToPointerRoot, X.CurrentTime)
            protocols = w.get_wm_protocols() or []
            take_focus = self.atom("WM_TAKE_FOCUS")
            if take_focus in protocols:
                ev = event.ClientMessage(window=w, client_type=self.atom("WM_PROTOCOLS"),
                                         data=(32, [take_focus, X.CurrentTime, 0, 0, 0]))
                w.send_event(ev)
        except error.XError:
            return
        old, self.active = self.active, wid
        if old and old != wid:
            self._grab_click(old, True)
        self._grab_click(wid, False)
        self._publish()

    def _iconify(self, wid: int) -> None:
        if wid not in self.clients or wid in self.iconic:
            return
        self.iconic.add(wid)
        self._expect_unmap.add(wid)
        try:
            self._win(wid).unmap()
            if wid in self.frames:
                self.frames[wid].unmap()
        except error.XError:
            pass
        self._set_state(wid, ICONIC)
        if self.active == wid:
            self.active = 0
            self.root.set_input_focus(X.PointerRoot, X.CurrentTime)
        self._publish()

    # ------------------------------------------------------------- events
    def handle(self, ev) -> None:
        t = ev.type
        if t == X.MapRequest:
            wid = ev.window.id
            try:
                ev.window.map()
            except error.XError:
                return
            self._manage(wid)
            self._activate(wid)
        elif t == X.ConfigureRequest:
            kw = {}
            m = ev.value_mask
            for bit, name in ((X.CWX, "x"), (X.CWY, "y"), (X.CWWidth, "width"), (X.CWHeight, "height"),
                              (X.CWBorderWidth, "border_width")):
                if m & bit:
                    kw[name] = getattr(ev, name)
            if m & X.CWStackMode:
                kw["stack_mode"] = ev.stack_mode
            f = self.frames.get(ev.window.id)
            try:
                if f is None:
                    ev.window.configure(**kw)
                else:
                    # x / y: where the client area goes; the frame goes round it
                    t, b = self.frame_top, self.frame_border
                    fkw = {}
                    if "x" in kw:
                        fkw["x"] = kw.pop("x") - b
                    if "y" in kw:
                        fkw["y"] = kw.pop("y") - t
                    if "width" in kw:
                        fkw["width"] = kw["width"] + 2 * b
                    if "height" in kw:
                        fkw["height"] = kw["height"] + t + b
                    if "stack_mode" in kw:
                        fkw["stack_mode"] = kw.pop("stack_mode")
                    kw.pop("border_width", None)
                    if fkw:
                        f.configure(**fkw)
                    if kw:
                        ev.window.configure(**kw)
                    self._send_configure_notify(ev.window.id)
            except error.XError:
                pass
        elif t == X.UnmapNotify:
            wid = ev.window.id
            if ev.event.id == wid:
                return                  # the copy sent to the window itself; its parent's copy counts
            if wid in self._expect_unmap:
                self._expect_unmap.discard(wid)
            elif wid in self.clients and wid not in self.iconic:
                self._set_state(wid, WITHDRAWN)
                self._forget(wid)
        elif t == X.DestroyNotify:
            self._forget(ev.window.id)
        elif t == X.ButtonPress:
            # passive grab on an inactive window: activate, then let the click through
            wid = ev.window.id
            if wid in self.clients:
                self._activate(wid)
            self.d.allow_events(X.ReplayPointer, X.CurrentTime)
        elif t == X.ClientMessage:
            name = self.d.get_atom_name(ev.client_type)
            wid = ev.window.id
            if name == "_NET_ACTIVE_WINDOW":
                if wid not in self.clients:
                    try:
                        if not self._win(wid).get_attributes().override_redirect:
                            self._manage(wid)
                    except error.XError:
                        return
                self._activate(wid)
            elif name == "WM_CHANGE_STATE" and ev.data[1][0] == ICONIC:
                self._iconify(wid)
        self.d.flush()

    def run(self) -> None:
        while True:
            try:
                ev = self.d.next_event()
            except (error.ConnectionClosedError, OSError):
                return
            try:
                self.handle(ev)
            except error.XError:
                continue


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--display", default=os.environ.get("DISPLAY"))
    ap.add_argument("--frame", default="", help="TOP,BORDER: title bar and border in px (default: none)")
    ap.add_argument("--composite", choices=("on", "off"), default="on")
    a = ap.parse_args(argv)
    frame = None
    if a.frame:
        top, border = (int(v) for v in a.frame.split(","))
        frame = (max(0, top), max(0, border))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    MiniWM(a.display, frame=frame, composite=a.composite == "on").run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
