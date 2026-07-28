#!/usr/bin/env python3

#############################################################
# Copyright 2026 Lawrence Livermore National Security, LLC
# (c.f. AUTHORS, NOTICE.LLNS, COPYING)
#
# This file is part of the Flux resource manager framework.
# For details, see https://github.com/flux-framework.
#
# SPDX-License-Identifier: LGPL-3.0
#############################################################

"""
brewcop touchscreen app.

A Kivy touchscreen coffee monitor for the Technivorm at B451.  Reads the
Avery Berkel scale and an i-Snail clamp on the boiler, and shows a live
carafe (level + freshness, with a blinking biohazard for a stale pot).

Brewing is detected automatically from boiler current (no BREW button, no
weight inference): a sustained draw arms brewing, and the pour settling
completes the batch to ready (see brains).  The batch then ages until the
coffee is poured out.

A fixed action rail (ZERO / WEIGH) sits on the right of every screen; the
layout never moves, so the verbs are always in the same place.

Screens (three total):
  Home     -- Flux mark + wordmark, the live carafe, and a pot-status line.
  Weigh    -- live scale weight, g/oz units toggle, tare, dosing hint, Back.
  Settings -- Slack on/off + steppers for tunable parameters (usersettings).

Data comes from a brewsource: the real ScaleBrewSource (scale + current ->
Brains -> potstate) in normal operation, or a MockBrewSource cycling canned
states under --mock (tap the mock strip to advance).

Notifications: on a brew reaching ready we notify Slack ONLY IF the user
setting slack_enabled is on (default OFF). Because ready follows a sensed
boiler cycle with a settled pour, it no longer storms on pours or placements.

Config: machine facts (serial port, webhook URL, location) come from
machineconfig (read-only /etc/brewcop/config.toml); tweakable preferences
from usersettings (writable JSON, saved on the Settings screen).

Usage:
    python3 brewcop.py [--mock] [--windowed]

Press 'q' or Escape to quit.  Targets the Kivy 2.1.0 API (Bookworm).
"""

import argparse
import os
import sys

# Kivy parses sys.argv itself at import time; without this it would choke on
# our --mock/--windowed flags and print its own usage message.  Must be set
# before `import kivy`.
os.environ.setdefault("KIVY_NO_ARGS", "1")

import kivy

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Rectangle, Line, Mesh, Ellipse
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image as ImageWidget
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.widget import Widget

import machineconfig
import usersettings
import potstate
import brewsource
import brewstate
from scale import open_scale, NoScale
from currentsensor import open_current_sensor, NoCurrentSensor
from backlight import Backlight

kivy.require("2.1.0")

# Image assets live in ./images relative to this script (repo root).
IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
BIOHAZARD_PNG = os.path.join(IMAGES_DIR, "biohazard.png")
FLUX_MARK_PNG = os.path.join(IMAGES_DIR, "flux-mark.png")

# --- palette -------------------------------------------------------------
# "Serious" dark theme: charcoal ground, restrained ink, one blue accent
# (roughly Flux blue) plus semantic green/amber/red for coffee state.
BG = (0.09, 0.10, 0.12, 1)  # near-black charcoal
PANEL = (0.14, 0.16, 0.19, 1)  # slightly raised card
INK = (0.90, 0.92, 0.94, 1)  # primary text
MUTED = (0.55, 0.60, 0.66, 1)  # secondary text
ACCENT = (0.29, 0.62, 1.00, 1)  # flux-ish blue
GREEN = (0.36, 0.78, 0.45, 1)  # fresh / ready
AMBER = (0.95, 0.72, 0.25, 1)  # aging / stale nag
RED = (0.92, 0.35, 0.35, 1)  # empty / kaput
HAZARD = (0.95, 0.85, 0.10, 1)  # biohazard yellow (stale warning)
GRAPHITE = (0.16, 0.17, 0.20, 1)  # carafe lid/handle/base (dark plastic;
# dark, but not pure black so it still
# reads against the charcoal background)
STEEL = (0.62, 0.66, 0.70, 1)  # brushed-steel carafe body


# Settings/config live in dedicated modules now: user-tweakable preferences
# in usersettings.UserSettings (writable JSON), machine facts in
# machineconfig (read-only TOML).


# --- graphics helpers ----------------------------------------------------
def _fill(widget, rgba, radius=0):
    """Paint a solid (optionally rounded) background behind a widget.

    Stashes both the Color instruction (widget._bg_color) and the shape
    (widget._bg) so callers can recolor or reposition later.  Graphics
    instructions are Cython objects with no __dict__, so rgba lives on the
    Color, not the shape.
    """
    with widget.canvas.before:
        widget._bg_color = Color(*rgba)
        if radius:
            widget._bg = RoundedRectangle(
                radius=[radius], pos=widget.pos, size=widget.size
            )
        else:
            widget._bg = Rectangle(pos=widget.pos, size=widget.size)

    def _sync(*_a):
        widget._bg.pos = widget.pos
        widget._bg.size = widget.size

    widget.bind(pos=_sync, size=_sync)


class FlatButton(Button):
    """A borderless, flat-styled button with our theme colors."""

    def __init__(self, bg=PANEL, fg=INK, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)  # draw our own
        self.color = fg
        self._bg_rgba = bg
        _fill(self, bg, radius=dp(10))
        self.bind(state=self._on_state)

    def _on_state(self, _w, state):
        # brighten the panel slightly while pressed, for touch feedback.
        r, g, b, a = self._bg_rgba
        if state == "down":
            self._bg_color.rgba = (
                min(r + 0.10, 1),
                min(g + 0.10, 1),
                min(b + 0.10, 1),
                a,
            )
        else:
            self._bg_color.rgba = self._bg_rgba


class CarafeWidget(Widget):
    """A stylized Technivorm thermal carafe that fills with coffee.

    `fill` is 0..1 (fraction full).  `coffee_rgba` colors the liquid (we
    tint it by freshness elsewhere: green fresh -> amber aging).  When
    `expired` is True and there is still coffee, a blinking biohazard symbol
    is overlaid as a "someone left the old pot" nag.

    The carafe is (necessarily) a stylized infographic: the real Moccamaster
    thermal carafe is opaque brushed steel, so the coffee level is shown as
    a fill inside the silhouette rather than a literal view.

    Everything is redrawn from scratch in _redraw() on any pos/size/state
    change.  Coordinates are computed relative to the widget box so it
    scales with the layout.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fill = 0.0
        self._coffee = GREEN
        self._expired = False
        self._blink_on = True
        self._blink_ev = None
        self._body = None  # geometry cached by _redraw
        # Age clock shown inside the body while coffee is fresh/aging (the
        # biohazard takes over once stale).
        self._age_text = ""
        self._age_s = None  # raw age seconds for the smiley clock (H:MM)
        self._needs_clean = False  # stale + coffee present -> biohazard
        self._brewing = False  # in-progress brew -> rain-cloud icon
        self._age_label = Label(
            text="",
            font_size=sp(30),
            bold=True,
            color=(1, 1, 1, 0.92),
            size_hint=(None, None),
            halign="center",
            valign="middle",
        )
        self._age_label.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(self._age_label)
        # Load the biohazard PNG once; tolerate it being absent (fall back to
        # no image -- the caption still conveys the warning).
        try:
            self._hazard_tex = CoreImage(BIOHAZARD_PNG).texture
        except Exception:
            self._hazard_tex = None
        self.bind(pos=self._redraw, size=self._redraw)

    # --- public state --------------------------------------------------
    def set_state(
        self,
        fill,
        coffee_rgba,
        expired,
        age_text="",
        age_s=None,
        needs_clean=False,
        brewing=False,
    ):
        self._fill = max(0.0, min(1.0, fill))
        self._coffee = coffee_rgba
        self._expired = expired  # current batch stale -> draw the body empty
        self._age_text = age_text  # shown inside the body while fresh/aging
        self._age_s = age_s  # raw seconds; drives the smiley clock (None=off)
        self._needs_clean = needs_clean  # stale + coffee present -> biohazard
        self._brewing = brewing  # in-progress brew -> rain-cloud icon
        # The blink clock drives two animations: the biohazard reminder (blinks
        # whenever a stale pot still has coffee in it) and the brewing
        # rain-cloud's falling drops.  Run it if either is active.
        show_hazard = needs_clean and self._fill > 0.02
        self._set_blinking(show_hazard or brewing)
        self._redraw()

    def _set_blinking(self, on):
        if on and self._blink_ev is None:
            self._blink_on = True
            self._blink_ev = Clock.schedule_interval(self._blink, 0.6)
        elif not on and self._blink_ev is not None:
            self._blink_ev.cancel()
            self._blink_ev = None
            self._blink_on = True

    def _blink(self, _dt):
        self._blink_on = not self._blink_on
        self._redraw()

    # --- drawing -------------------------------------------------------
    def _redraw(self, *_a):
        # Draw all carafe art into canvas.before and clear only that.  Child
        # widgets (the target + age labels) live in self.canvas via
        # add_widget(); clearing self.canvas would wipe them (they are only
        # added once), which is why they must NOT be cleared here.
        self.canvas.before.clear()
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return

        # Technivorm Moccamaster thermal carafe silhouette (traced from the
        # product photo).  Distinctive features:
        #   - body tapers OUTWARD toward the base (A-line: wider at bottom)
        #   - black base ring at the bottom
        #   - wide black flip-lid on top with a pour spout on the LEFT
        #   - an angular, cantilevered handle on the RIGHT, attached only at
        #     the top: it juts right then drops straight down, squared off,
        #     ending free (does NOT loop back to the body)
        # All proportions relative to the widget box.
        cx = x + w * 0.46  # body center (left of middle;
        top_w = w * 0.34  #   handle occupies the right)
        base_w = w * 0.44  # base wider than top -> taper
        body_h = h * 0.64
        by = y + h * 0.06  # bottom of the steel body
        top = by + body_h

        tl, tr = cx - top_w / 2, cx + top_w / 2  # top left/right
        bl, br = cx - base_w / 2, cx + base_w / 2  # base left/right
        # body trapezoid (CCW): bottom-left, bottom-right, top-right, top-left
        body_pts = [bl, by, br, by, tr, top, tl, top]

        base_h = h * 0.045
        lid_w = top_w * 1.12
        lid_h = h * 0.075
        lx = cx - lid_w / 2
        ly = top - lid_h * 0.20

        # Cache body geometry so touch handling can map y <-> fill fraction.
        # The fillable band runs from (by+inset) up to (by+inset+fill_max),
        # matching the coffee-fill drawing below.
        inset = dp(5)
        fill_max = body_h - 2 * inset
        self._body = {
            "cx": cx,
            "top_w": top_w,
            "base_w": base_w,
            "by": by,
            "body_h": body_h,
            "inset": inset,
            "fill_max": fill_max,
        }

        with self.canvas.before:
            # --- angular cantilevered handle on the right (drawn first so
            # the body overlaps its inner end).  Juts right from the top,
            # then drops straight down; squared, chunky, ends free. ---
            Color(*GRAPHITE)
            hb = dp(6)  # handle bar thickness
            h_top = top - lid_h * 0.15  # height of the top bar
            h_out = br + w * 0.13  # outer edge of the vertical arm
            h_bot = by + body_h * 0.34  # bottom of the vertical arm
            Line(
                points=[cx, h_top, h_out, h_top, h_out, h_bot],
                width=hb,
                joint="miter",
                cap="square",
            )

            # --- steel body (tapered), filled to its exact trapezoid ---
            Color(*STEEL)
            self._poly(body_pts)

            # --- coffee fill: a trapezoidal slice from the base up, so the
            # liquid follows the body taper (wider at the bottom).  When the
            # pot is expired we deliberately draw it EMPTY (no fill): brown
            # just looks like coffee, so we let the biohazard symbol in an
            # empty steel carafe carry the "don't drink this" message. ---
            if self._fill > 0.01 and not self._expired:
                fh = fill_max * self._fill
                yb = by + inset
                yt = yb + fh
                # interpolate body half-width at yb and yt

                def half_w(yy):
                    t = (yy - by) / body_h
                    bw = (base_w - top_w) * (1 - t) + top_w
                    return bw / 2 - inset

                Color(*self._coffee)
                self._poly(
                    [
                        cx - half_w(yb),
                        yb,
                        cx + half_w(yb),
                        yb,
                        cx + half_w(yt),
                        yt,
                        cx - half_w(yt),
                        yt,
                    ]
                )

            # --- body outline (over the fill for a crisp steel edge) ---
            Color(*INK)
            Line(points=body_pts + [bl, by], width=dp(1.6), joint="miter", cap="square")

            # --- base ring ---
            Color(*GRAPHITE)
            self._poly(
                [
                    bl,
                    by,
                    br,
                    by,
                    br - base_w * 0.03,
                    by - base_h,
                    bl + base_w * 0.03,
                    by - base_h,
                ]
            )

            # --- flip-lid with left pour spout ---
            Color(*GRAPHITE)
            RoundedRectangle(pos=(lx, ly), size=(lid_w, lid_h), radius=[lid_h * 0.35])
            # spout: a small triangle off the lid's left edge
            self._poly(
                [
                    lx,
                    ly + lid_h * 0.15,
                    lx - lid_w * 0.16,
                    ly + lid_h * 0.55,
                    lx,
                    ly + lid_h * 0.9,
                ]
            )

        # Biohazard overlay: a stale batch with coffee still in the pot
        # (needs_clean).  It is recomputed each tick, so it blinks over a stale
        # pot and clears itself the moment the coffee is poured out.
        if self._needs_clean and self._fill > 0.02:
            body_cy = by + body_h * 0.42
            self._draw_hazard(cx, body_cy, min(top_w, base_w), body_h)

        # Brewing: a rain-cloud ABOVE the carafe, drops falling down into the
        # lid -- reads as the machine raining coffee into the pot.  Sits in the
        # headroom over the lid; drops fall from the cloud down to the pour
        # spout.  Immediate feedback the moment the boiler is sensed running;
        # yields to the biohazard just like the smiley does.
        if self._brewing and not self._needs_clean:
            lid_top = ly + lid_h
            head = (y + h) - lid_top  # free space above the lid
            cloud_cy = lid_top + head * 0.62  # cloud sits high in the headroom
            self._draw_brewing(cx, cloud_cy, top_w * 1.25, lid_top)

        # Fresh/aging coffee: draw a smiley in the SAME spot the biohazard
        # would go (the happy counterpart to "gone bad").  Below it, an elapsed
        # clock in H:MM.  The biohazard takes precedence: while it is up (stale
        # coffee still in the pot), no smiley.
        if self._age_text and not self._needs_clean:
            body_cy = by + body_h * 0.42
            self._draw_smiley(cx, body_cy, min(top_w, base_w), body_h)
            if self._age_s is not None and self._age_s >= 60:
                self._age_label.text = self._fmt_clock(self._age_s)
                self._age_label.size = (min(top_w, base_w), dp(36))
                # sit the clock just below the smiley
                self._age_label.pos = (
                    cx - min(top_w, base_w) / 2,
                    body_cy
                    - min(min(top_w, base_w) * 0.72, body_h * 0.44) / 2
                    - dp(34),
                )
                self._age_label.opacity = 1
            else:
                self._age_label.opacity = 0
        else:
            self._age_label.opacity = 0

    @staticmethod
    def _fmt_clock(seconds):
        """Elapsed time as H:MM (e.g. 0:05, 1:23) for the smiley clock."""
        m = int(max(0, seconds)) // 60
        return "{}:{:02d}".format(m // 60, m % 60)

    def _draw_smiley(self, cxc, cyc, ref_w, ref_h):
        """Happy face inside the carafe body -- the fresh-coffee counterpart to
        the biohazard, drawn at the same location.  `cxc, cyc` is the center."""
        side = min(ref_w * 0.72, ref_h * 0.44)
        r = side / 2.0
        lw = dp(3)
        with self.canvas.before:
            Color(*INK)
            # face circle (Line ellipse via a rounded... use Line circle)
            Line(circle=(cxc, cyc, r), width=lw)
            # eyes
            eye_r = r * 0.12
            ey = cyc + r * 0.28  # up (Kivy y is up)
            for ex in (cxc - r * 0.34, cxc + r * 0.34):
                Ellipse(pos=(ex - eye_r, ey - eye_r), size=(eye_r * 2, eye_r * 2))
            # smile: lower arc (Kivy Line circle angles in degrees, 0 at 12
            # o'clock, clockwise; 180 is the bottom).  120..240 sweeps a
            # symmetric bottom arc -> an upturned smile.
            Line(circle=(cxc, cyc + r * 0.05, r * 0.55, 120, 240), width=lw)

    def _draw_brewing(self, cxc, ccy, ref_w, rain_to_y):
        """Rain-cloud ABOVE the carafe -- the brewing counterpart to the smiley:
        a puffy cloud with drops falling down into the pot.  `cxc, ccy` is the
        cloud center; drops fall from just under the cloud down to `rain_to_y`
        (the pot mouth).  The drops shift with the blink clock so the rain
        animates."""
        r = (ref_w * 0.5) / 2.0
        ccx = cxc
        with self.canvas.before:
            # puffy cloud: overlapping filled ellipses + a flat bottom slab.
            # (ox, oy, radius) in units of r, oy positive = up.
            Color(*INK)
            for ox, oy, pr in (
                (-0.55, -0.05, 0.42),
                (0.0, 0.25, 0.55),
                (0.6, -0.05, 0.45),
                (-0.15, -0.20, 0.50),
                (0.3, -0.22, 0.48),
            ):
                px, py, pr_px = ccx + ox * r, ccy + oy * r, pr * r
                Ellipse(pos=(px - pr_px, py - pr_px), size=(pr_px * 2, pr_px * 2))
            RoundedRectangle(
                pos=(ccx - r * 0.72, ccy - r * 0.45),
                size=(r * 1.47, r * 0.5),
                radius=[r * 0.12],
            )
            # falling drops (blue) streaming from the cloud down toward the pot
            # mouth.  Each drop wraps within the gap; the blink clock gives it a
            # simple two-phase downward crawl so the rain reads as moving.
            Color(*ACCENT)
            cloud_bottom = ccy - r * 0.5
            gap = max(dp(12), cloud_bottom - rain_to_y)
            dl = min(r * 0.4, gap * 0.28)  # drop length
            phase = (0.0 if self._blink_on else 0.5) * (gap / 2.0)
            for i, ox in enumerate((-0.4, 0.05, 0.5)):
                dx = ccx + ox * r
                # stagger the three streams, then crawl them down with phase,
                # wrapping back up so drops keep flowing rather than draining
                offset = (i * gap / 3.0 + phase) % (gap / 2.0)
                dy = cloud_bottom - offset
                Line(points=[dx, dy, dx, dy - dl], width=dp(2.5), cap="round")

    def _poly(self, pts):
        """Fill a convex polygon (flat x,y list) via a Mesh triangle fan,
        so the tapered carafe silhouette is filled exactly rather than by a
        bounding box.  Uses the polygon centroid as the fan hub."""
        xs = pts[0::2]
        ys = pts[1::2]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        verts = [cx, cy, 0, 0]
        n = len(xs)
        for i in range(n):
            verts += [xs[i], ys[i], 0, 0]
        # fan indices: center, each edge vertex, wrapping back to the first
        indices = []
        for i in range(1, n + 1):
            nxt = i + 1 if i < n else 1
            indices += [0, i, nxt]
        Mesh(vertices=verts, indices=indices, mode="triangles")

    def _draw_hazard(self, cxc, cyc, ref_w, ref_h):
        # Blinking biohazard symbol placed INSIDE the carafe body.  No scrim
        # and no caption widget (the Home status line already says "Stale
        # coffee - please dump"); the yellow trefoil (transparent PNG) sits
        # in the empty steel body.  `cxc, cyc` is the symbol center.
        if self._blink_on and self._hazard_tex is not None:
            side = min(ref_w * 0.86, ref_h * 0.5)
            sx = cxc - side / 2.0
            sy = cyc - side / 2.0
            with self.canvas.before:
                Color(1, 1, 1, 1)  # texture already carries its own color
                Rectangle(texture=self._hazard_tex, pos=(sx, sy), size=(side, side))


class Header(BoxLayout):
    """Small top bar: back button (optional) + screen title."""

    def __init__(self, text, on_back=None, **kwargs):
        super().__init__(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(56),
            padding=[dp(12), 0],
            spacing=dp(8),
            **kwargs
        )
        if on_back:
            back = FlatButton(
                text="< Back",
                bg=BG,
                fg=ACCENT,
                size_hint_x=None,
                width=dp(120),
                font_size=sp(18),
            )
            back.bind(on_release=lambda *_a: on_back())
            self.add_widget(back)
        else:
            self.add_widget(Widget(size_hint_x=None, width=dp(120)))
        self.add_widget(Label(text=text, color=MUTED, font_size=sp(18)))
        self.add_widget(Widget(size_hint_x=None, width=dp(120)))


# --- Home ----------------------------------------------------------------
# Faked physical pot states, cycled by tapping the status card.  These
# reflect what is physically on the scale, NOT any monitoring session --
# so a stale pot keeps showing here (a "someone dump the old pot" nag)
# even after the brew session's 3h timeout.
# Each faked state: (key, text color, status text, carafe fill 0..1,
# coffee color, expired?).  fill<0 means "no carafe on the scale".
# Per-state text color for the Home status line, keyed by PotState.key.
STATE_COLOR = {
    "no_pot": MUTED,
    "empty": MUTED,
    "present": INK,  # coffee present, age unknown
    "fresh": GREEN,
    "aging": AMBER,
    "brewing": ACCENT,
    "stale": AMBER,
}


# Canned states for --mock mode (tap the carafe to cycle through them).
def mock_states():
    return [
        potstate.PotState("no_pot", "No pot on scale", 0.0, False),
        potstate.PotState("empty", "Empty pot", 0.02, False),
        potstate.PotState("present", "Coffee: ~0.94 L - age unknown", 0.75, False),
        potstate.PotState("fresh", "Coffee: ~0.94 L - fresh (12 min)", 0.75, False),
        potstate.PotState("brewing", "Brewing - 3 min", 0.40, False),
        potstate.PotState("aging", "Coffee: ~0.70 L - aging (2h 10m)", 0.56, False),
        potstate.PotState("stale", "Stale coffee - please dump (4h 20m)", 0.60, True),
    ]


class HomeScreen(Screen):
    def __init__(self, go, app, **kwargs):
        super().__init__(**kwargs)
        self.go = go
        self.app = app  # for source.poll(), notify(), settings
        self._last_key = None  # for wake-on-event edge detection
        self._flashing = False  # suppress status updates while flashing a msg
        self._last_grams = 0.0  # last valid raw reading, held while moving
        root = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))

        # top bar: title centered across the FULL width (badge + settings
        # float over the ends), so the wordmark is truly screen-centered.
        top = FloatLayout(size_hint_y=None, height=dp(52))
        title = Label(
            text="B R E W C O P",
            color=GREEN,
            font_size=sp(28),
            bold=True,
            halign="center",
            valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        title.bind(size=lambda w, s: setattr(w, "text_size", s))
        top.add_widget(title)
        # Flux mark at the left (falls back to nothing if the asset is
        # missing, so the layout still holds).
        if os.path.exists(FLUX_MARK_PNG):
            badge = ImageWidget(
                source=FLUX_MARK_PNG,
                size_hint=(None, None),
                size=(dp(44), dp(44)),
                allow_stretch=True,
                keep_ratio=True,
                pos_hint={"x": 0, "center_y": 0.5},
            )
            top.add_widget(badge)
        gear = FlatButton(
            text="Settings",
            bg=BG,
            fg=ACCENT,
            size_hint=(None, None),
            size=(dp(110), dp(40)),
            pos_hint={"right": 1, "center_y": 0.5},
            font_size=sp(16),
        )
        gear.bind(on_release=lambda *_a: go("settings"))
        top.add_widget(gear)
        root.add_widget(top)

        # Carafe centerpiece.  Brewing is auto-detected from boiler current, so
        # there is nothing to dial here; the ZERO/WEIGH actions live in the
        # app-level rail to the right of all screens.
        center = FloatLayout()
        self.carafe = CarafeWidget(size_hint=(None, None))

        def _place_carafe(*_a):
            side = min(center.width * 0.62, center.height * 0.88)
            self.carafe.size = (side, side * 1.05)
            self.carafe.pos = (
                center.x + (center.width - self.carafe.width) / 2,
                center.y + (center.height - self.carafe.height) / 2,
            )

        center.bind(pos=_place_carafe, size=_place_carafe)
        center.add_widget(self.carafe)
        root.add_widget(center)

        # Status row (full width, below the body): pot-status centered, with
        # the raw sensor readouts stacked together on the right -- weight on
        # top, live boiler current directly below it (one "source of truth"
        # block rather than split across the row).
        statusbar = FloatLayout(size_hint_y=None, height=dp(40))
        self.status = Label(
            text="",
            font_size=sp(20),
            bold=True,
            halign="center",
            valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        self.status.bind(size=lambda w, s: setattr(w, "text_size", s))
        statusbar.add_widget(self.status)
        self.weight_lbl = Label(
            text="",
            color=MUTED,
            font_size=sp(16),
            halign="right",
            valign="middle",
            size_hint=(None, None),
            size=(dp(110), dp(20)),
            pos_hint={"right": 1, "top": 1},
        )
        self.weight_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        statusbar.add_widget(self.weight_lbl)
        # Boiler current (amps), directly below the weight (same right edge).
        # Blank until the sensor gives a reading.
        self.amps_lbl = Label(
            text="",
            color=MUTED,
            font_size=sp(16),
            halign="right",
            valign="middle",
            size_hint=(None, None),
            size=(dp(110), dp(20)),
            pos_hint={"right": 1, "y": 0},
        )
        self.amps_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        statusbar.add_widget(self.amps_lbl)
        # Left mirror of the right block: the stored pot tare on top and the
        # live delta from it (net contents) below.  Same "source of truth"
        # idea -- the tare the ZERO button would (or did) capture, and how far
        # the scale is from it right now, so a greyed ZERO reads as "nothing to
        # zero" rather than a dead button.
        self.tare_lbl = Label(
            text="",
            color=MUTED,
            font_size=sp(16),
            halign="left",
            valign="middle",
            size_hint=(None, None),
            size=(dp(110), dp(20)),
            pos_hint={"x": 0, "top": 1},
        )
        self.tare_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        statusbar.add_widget(self.tare_lbl)
        self.delta_lbl = Label(
            text="",
            color=MUTED,
            font_size=sp(16),
            halign="left",
            valign="middle",
            size_hint=(None, None),
            size=(dp(110), dp(20)),
            pos_hint={"x": 0, "y": 0},
        )
        self.delta_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        statusbar.add_widget(self.delta_lbl)
        root.add_widget(statusbar)

        # In --mock mode only, a strip to advance the canned states.
        if self.app.mock:
            cycle = FlatButton(
                text="(mock: tap to cycle states)",
                bg=BG,
                fg=MUTED,
                font_size=sp(12),
                size_hint_y=None,
                height=dp(24),
            )
            cycle.bind(on_release=lambda *_a: self._cycle())
            root.add_widget(cycle)

        self.add_widget(root)
        self._pot = potstate.PotState("no_pot", "", 0.0, False)
        self.tick()

    def tick(self, *_a):
        """Poll the source, render, fire events."""
        # Don't poll the shared serial port while another screen (Weigh) is
        # polling it directly -- two readers would corrupt each other's ECR
        # responses.  Home only ticks while it is the visible screen.
        if self.manager is not None and self.manager.current != self.name:
            return
        result = self.app.source.poll()
        pot = result.pot_state

        self._pot = pot
        self._render(pot, boiler_on=result.boiler_on)
        # Let the app refresh the rail's enabled/disabled buttons.
        self.app.refresh_rail()
        # Live weight readout beside the status line.  While the scale is
        # moving we hold the last good grams rather than flashing "settling"
        # (which was jarring) -- same as the Weigh screen, which just keeps its
        # last valid value.  Blank only until we've had a first reading.
        if result.raw_grams is not None:
            self._last_grams = result.raw_grams
        self.weight_lbl.text = "{:.0f} g".format(self._last_grams)
        # Live boiler current beside the weight.  Blank when there's no sensor
        # (NoCurrentSensor reads None) rather than showing a fake 0.
        if result.amps is not None:
            self.amps_lbl.text = "{:.1f} A".format(result.amps)
        # Left block: stored tare + live delta from it (mirrors weight/amps).
        tare = self.app.settings["pot_tare_g"]
        self.tare_lbl.text = "tare {:.0f} g".format(tare)
        self.delta_lbl.text = "Δ {:+.0f} g".format(self._last_grams - tare)

        # Notify + wake on the brewing->ready transition (app gates Slack).
        if result.event == "ready":
            self.app.on_ready_event(result)
        # Wake the screen on any state change worth noticing.
        if pot.key != self._last_key:
            self.app.wake(pot)
        self._last_key = pot.key

    def _cycle(self):
        # --mock only: advance canned states.
        self.app.source.advance()
        self.tick()

    def _flash(self, msg, seconds=2.0):
        # Briefly show a message on the status line, holding it against the
        # periodic tick, then resume normal status.
        self._flashing = True
        self.status.text = msg
        self.status.color = HAZARD

        def _end(*_a):
            self._flashing = False
            self.tick()

        Clock.schedule_once(_end, seconds)

    def _render(self, pot, boiler_on=False):
        # Two carafe signals: `expired` empties the body when the current batch
        # is stale (stale coffee shouldn't look drinkable); `needs_clean` drives
        # the biohazard reminder (stale + coffee still in the pot), recomputed
        # each tick so it clears itself once the pot is emptied.
        expired = pot.expired
        # No persistent status text: the carafe (fill + smiley/clock +
        # biohazard) and the weight readout carry the state visually.  The
        # status label is used only for transient flash messages; clear it when
        # not flashing.
        if not self._flashing:
            self.status.text = ""

        # Always draw the carafe (even with no pot on the scale it shows as an
        # empty carafe -- the fixed frame the fill relates to).  While
        # fresh/aging (and not stale) the carafe shows a smiley plus an H:MM
        # clock once a minute has passed; age_text is just the on/off marker
        # for that, age_s drives the clock.  With no pot on the scale, ghost
        # the whole carafe back so it reads as absent, not present-and-empty.
        self.carafe.opacity = 0.35 if pot.key == "no_pot" else 1.0
        coffee = AMBER if pot.key == "aging" else GREEN
        age_text = pot.key if pot.key in ("fresh", "aging") else ""
        # Show the rain cloud whenever the heater is drawing current, not just
        # once the state machine has armed "brewing" -- boiler_on leads the
        # brewing state by BREW_ON_DEBOUNCE_S, so the rain starts the instant
        # the Moccamaster switches on, and keeps falling through the post-boiler
        # settle until the batch goes ready.
        brewing = boiler_on or pot.key == "brewing"
        self.carafe.set_state(
            pot.fill,
            coffee,
            expired,
            age_text=age_text,
            age_s=pot.age_s,
            needs_clean=pot.needs_clean,
            brewing=brewing,
        )
        # Actions live in the app-level rail (ZERO / WEIGH); nothing to do here.


# --- Weigh ---------------------------------------------------------------
class WeighScreen(Screen):
    def __init__(self, go, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.config = app.settings
        self.units = "g"
        self._grams = 0.0  # last live reading (raw grams, pre-tare)
        self._offset = 0.0  # tare offset
        self._poll_ev = None
        root = BoxLayout(orientation="vertical")
        root.add_widget(Header("WEIGH", on_back=lambda: go("home")))

        body = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))

        self.readout = Label(text="", color=GREEN, font_size=sp(96), bold=True)
        body.add_widget(self.readout)

        units = BoxLayout(
            orientation="horizontal", spacing=dp(12), size_hint_y=None, height=dp(64)
        )
        self.btn_g = FlatButton(text="grams", font_size=sp(20))
        self.btn_oz = FlatButton(text="ounces", font_size=sp(20))
        self.btn_g.bind(on_release=lambda *_a: self._set_units("g"))
        self.btn_oz.bind(on_release=lambda *_a: self._set_units("oz"))
        units.add_widget(self.btn_g)
        units.add_widget(self.btn_oz)
        body.add_widget(units)

        tare = FlatButton(
            text="TARE",
            bg=ACCENT,
            fg=(1, 1, 1, 1),
            font_size=sp(24),
            bold=True,
            size_hint_y=None,
            height=dp(72),
        )
        tare.bind(on_release=lambda *_a: self._tare())
        body.add_widget(tare)

        # Dosing hint -- computed from the configured pot capacity (see
        # _refresh_hint), so it tracks the "Pot capacity" setting.
        hint = BoxLayout(
            orientation="vertical", padding=dp(16), size_hint_y=None, height=dp(96)
        )
        _fill(hint, PANEL, radius=dp(10))
        self.hint_cap = Label(
            text="", color=MUTED, font_size=sp(16), size_hint_y=None, height=dp(24)
        )
        hint.add_widget(self.hint_cap)
        self.hint_beans = Label(text="", color=INK, font_size=sp(22), bold=True)
        hint.add_widget(self.hint_beans)
        body.add_widget(hint)

        root.add_widget(body)
        self.add_widget(root)
        self._set_units("g")
        self._refresh_hint()

    def on_pre_enter(self, *_a):
        # Recompute the dosing hint (pot capacity may have changed), and poll
        # the scale live while this screen is showing.
        self._refresh_hint()
        if self._poll_ev is None:
            self._poll_ev = Clock.schedule_interval(self._poll, 0.5)

    def on_leave(self, *_a):
        if self._poll_ev is not None:
            self._poll_ev.cancel()
            self._poll_ev = None

    def _poll(self, *_a):
        try:
            self.app.scale.poll()
            if self.app.scale.weight_is_valid:
                self._grams = self.app.scale.weight
        except Exception:
            pass  # keep last reading on a transient serial hiccup
        self._refresh()

    def _refresh_hint(self):
        cap = self.config["pot_capacity_ml"]
        # SCA "golden ratio" ~1:18 by weight (1 g coffee per 18 g/mL water);
        # show a mild->strong band from 1:20 to 1:16 around the 1:18 anchor.
        self.hint_cap.text = "Full {:.2f} L pot".format(cap / 1000.0)
        self.hint_beans.text = "~{:.0f}-{:.0f} g beans   (SCA 1:18 = {:.0f} g)".format(
            cap / 20.0, cap / 16.0, cap / 18.0
        )

    def _set_units(self, u):
        self.units = u
        self._refresh()

    def _tare(self):
        self._offset = self._grams
        self._refresh()

    def _refresh(self):
        net = self._grams - self._offset
        if self.units == "g":
            self.readout.text = "{:.0f} g".format(net)
        else:
            self.readout.text = "{:.2f} oz".format(net / 28.3495)
        self.btn_g.color = ACCENT if self.units == "g" else INK
        self.btn_oz.color = ACCENT if self.units == "oz" else INK


# --- Settings ------------------------------------------------------------
def setting_label(text, markup=False):
    """Left-aligned row title used identically by every settings row, so the
    left edges line up.  halign only takes effect once text_size is bound to
    the widget size."""
    lbl = Label(
        text=text,
        color=INK,
        font_size=sp(18),
        markup=markup,
        halign="left",
        valign="middle",
    )
    lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
    return lbl


class StepperRow(BoxLayout):
    """A compact numeric setting: name on the left, then a [-] value [+]
    stepper.  Touch-friendly and precise -- one row per setting, far less
    vertical space and fussiness than a slider."""

    def __init__(self, name, config, key, vmin, vmax, step, fmt, note="", **kwargs):
        super().__init__(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(60),
            spacing=dp(8),
            **kwargs
        )
        self.config = config
        self.key = key
        self.vmin, self.vmax, self.step, self.fmt = vmin, vmax, step, fmt

        # Name with optional inline note (e.g. scale tolerance) via markup,
        # kept on ONE line to conserve vertical space.
        text = name
        if note:
            text += "  [color=888888][size=13]{}[/size][/color]".format(note)
        self.add_widget(setting_label(text, markup=True))

        minus = FlatButton(
            text="-",
            bg=PANEL,
            font_size=sp(30),
            bold=True,
            size_hint_x=None,
            width=dp(56),
        )
        minus.bind(on_release=lambda *_a: self._step(-1))
        self.add_widget(minus)

        self.value_lbl = Label(
            text="",
            color=ACCENT,
            font_size=sp(20),
            bold=True,
            size_hint_x=None,
            width=dp(96),
            halign="center",
            valign="middle",
        )
        self.value_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.add_widget(self.value_lbl)

        plus = FlatButton(
            text="+",
            bg=PANEL,
            font_size=sp(30),
            bold=True,
            size_hint_x=None,
            width=dp(56),
        )
        plus.bind(on_release=lambda *_a: self._step(+1))
        self.add_widget(plus)

        self._render()

    def _step(self, sign):
        v = self.config[self.key] + sign * self.step
        v = max(self.vmin, min(self.vmax, v))
        # round to the step grid to avoid float drift
        v = round(v / self.step) * self.step
        self.config[self.key] = v
        self._render()

    def _render(self):
        self.value_lbl.text = self.fmt(self.config[self.key])


class ToggleRow(BoxLayout):
    """A labeled on/off toggle for a boolean config key."""

    def __init__(self, name, config, key, **kwargs):
        super().__init__(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(60),
            spacing=dp(12),
            **kwargs
        )
        self.config = config
        self.key = key
        self.add_widget(setting_label(name))
        self.btn = FlatButton(
            text="",
            bg=PANEL,
            font_size=sp(18),
            bold=True,
            size_hint_x=None,
            width=dp(120),
        )
        self.btn.bind(on_release=lambda *_a: self._toggle())
        self.add_widget(self.btn)
        self._render()

    def _toggle(self):
        self.config[self.key] = not self.config[self.key]
        self._render()

    def _render(self):
        on = bool(self.config[self.key])
        self.btn.text = "ON" if on else "OFF"
        self.btn.color = GREEN if on else MUTED


class SettingsScreen(Screen):
    def __init__(self, go, config, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        root = BoxLayout(orientation="vertical")
        root.add_widget(Header("SETTINGS", on_back=lambda: go("home")))

        # Scrollable list of setting rows, so it can never overflow into the
        # header no matter how many rows or what the screen orientation is.
        rows = BoxLayout(
            orientation="vertical",
            padding=[dp(24), dp(8)],
            spacing=dp(10),
            size_hint_y=None,
        )
        rows.bind(minimum_height=rows.setter("height"))

        rows.add_widget(ToggleRow("Slack announcements", config, "slack_enabled"))
        rows.add_widget(
            StepperRow(
                "Stale timeout",
                config,
                "stale_hours",
                1.0,
                8.0,
                0.5,
                lambda v: "{:.1f} h".format(v),
            )
        )
        # Pot tare and empty threshold are no longer tunable here: the ZERO
        # button captures an accurate tare per brew (persisted), and the empty
        # threshold is a fixed constant now that the tare is trustworthy.
        rows.add_widget(
            StepperRow(
                "Pot capacity",
                config,
                "pot_capacity_ml",
                1000,
                1500,
                10,
                lambda v: "{:.0f} mL".format(v),
            )
        )

        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(rows)
        root.add_widget(scroll)

        # Save area pinned below the scroll list.
        self.save_msg = Label(
            text="", color=GREEN, font_size=sp(14), size_hint_y=None, height=dp(22)
        )
        root.add_widget(self.save_msg)

        save = FlatButton(
            text="SAVE",
            bg=ACCENT,
            fg=(1, 1, 1, 1),
            font_size=sp(24),
            bold=True,
            size_hint_y=None,
            height=dp(64),
        )
        save.bind(on_release=lambda *_a: self._save())
        root.add_widget(save)

        self.add_widget(root)

    def _save(self):
        try:
            self.config.save()
            self.save_msg.color = GREEN
            self.save_msg.text = "Saved."
        except OSError as e:
            self.save_msg.color = RED
            self.save_msg.text = "Save failed: {}".format(e)


TICK_PERIOD = 0.5  # seconds between Home scale polls


class BrewcopApp(App):
    title = "brewcop"

    def __init__(self, mock=False, **kwargs):
        super().__init__(**kwargs)
        self.mock = mock

    def build(self):
        # Config: machine facts (read-only) + user settings (writable).
        self.machine = machineconfig.load()
        self.settings = usersettings.UserSettings()

        # Scale + brew source.  --mock uses canned states and no hardware.
        if self.mock:
            self.scale = NoScale()
            self.current_sensor = NoCurrentSensor()
            self.source = brewsource.MockBrewSource(mock_states())
        else:
            self.scale, err = open_scale(self.machine.serial_port)
            if err:
                # Not fatal: fall back to NoScale so the UI still comes up.
                print(
                    "scale: {} (running without hardware)".format(err), file=sys.stderr
                )
            # Boiler current sensor (i-Snail on a VINT hub).  Also non-fatal:
            # NoCurrentSensor keeps the UI up, the current readout just blanks.
            self.current_sensor, cerr = open_current_sensor()
            if cerr:
                print(
                    "current sensor: {} (running without it)".format(cerr),
                    file=sys.stderr,
                )
            self.source = brewsource.ScaleBrewSource(
                self.scale,
                self.settings,
                tick_period=TICK_PERIOD,
                persist=brewstate,  # brew age survives reboots
                current_sensor=self.current_sensor,
            )

        # Backlight (safe no-op if the sysfs node is absent/unwritable).
        self.backlight = Backlight()
        self._dimmed = False
        self._idle_ev = None

        Window.clearcolor = BG
        if Window is not None:
            Window.bind(on_key_down=self._on_key_down)
            Window.bind(on_touch_down=self._on_touch)

        # Fixed action rail (ZERO / WEIGH), present on every screen.  Brewing
        # is auto-detected, so there is no BREW or CLEAN button; the two verbs
        # never move (spatial muscle memory).  Built BEFORE the screens because
        # HomeScreen's first tick() calls refresh_rail(), which touches these.
        rail = BoxLayout(
            orientation="vertical",
            spacing=dp(12),
            padding=[0, dp(24)],
            size_hint_x=None,
            width=dp(150),
        )
        self._zero_btn = FlatButton(
            text="ZERO\nEMPTY POT",
            bg=ACCENT,
            fg=(1, 1, 1, 1),
            font_size=sp(20),
            bold=True,
            halign="center",
        )
        self._zero_btn.bind(on_release=lambda *_a: self._rail_zero())
        self._weigh_btn = FlatButton(
            text="WEIGH\nBEANS",
            bg=PANEL,
            fg=INK,
            font_size=sp(20),
            bold=True,
            halign="center",
        )
        self._weigh_btn.bind(on_release=lambda *_a: self._go("weigh"))
        # WEIGH on top (the everyday verb), ZERO below it (occasional setup).
        for b in (self._weigh_btn, self._zero_btn):
            rail.add_widget(b)

        # Screens (HomeScreen.tick() -> refresh_rail() needs the buttons above).
        self.sm = ScreenManager(transition=SlideTransition(duration=0.2))
        go = self._go
        self.home = HomeScreen(go, self, name="home")
        self.sm.add_widget(self.home)
        self.sm.add_widget(WeighScreen(go, self, name="weigh"))
        self.sm.add_widget(SettingsScreen(go, self.settings, name="settings"))

        outer = BoxLayout(orientation="horizontal")
        outer.add_widget(self.sm)
        outer.add_widget(rail)

        # Drive the Home brew tick continuously (Home is the always-on view).
        # In --mock, don't auto-advance: the user taps to cycle.
        if not self.mock:
            Clock.schedule_interval(self.home.tick, TICK_PERIOD)

        self._reset_idle_timer()
        self.refresh_rail()
        return outer

    # --- events from the Home screen ----------------------------------
    def on_ready_event(self, result):
        """A brewing->ready transition happened.  Notify Slack IF enabled."""
        self.wake(result.pot_state)
        if not self.settings["slack_enabled"]:
            return
        url = self.machine.slack_webhook_url
        if not url:
            return
        self._notify_slack(url, result)

    def _notify_slack(self, url, result):
        # Imported lazily so the app runs without requests on a dev box.
        try:
            import requests

            ml = result.raw_grams or 0.0  # ~1 g per mL
            msg = "{:.0f} mL of fresh coffee is ready in {}.".format(
                ml, self.machine.location
            )
            requests.post(url, json={"text": msg}, timeout=5)
        except Exception as e:
            print("slack notify failed: {}".format(e), file=sys.stderr)

    # --- backlight inactivity dimming ---------------------------------
    def wake(self, pot=None):
        """Brighten to full and restart the idle timer."""
        if self._dimmed:
            self.backlight.set_level(1.0)
            self._dimmed = False
        self._reset_idle_timer()

    def _reset_idle_timer(self):
        if self._idle_ev is not None:
            self._idle_ev.cancel()
            self._idle_ev = None
        timeout = self.settings["dim_timeout_s"]
        if timeout and timeout > 0:
            self._idle_ev = Clock.schedule_once(self._dim, timeout)

    def _dim(self, *_a):
        self.backlight.set_level(self.settings["dim_level"])
        self._dimmed = True

    def _on_touch(self, _window, touch):
        # Any touch wakes the screen.  If we were dimmed, swallow this touch
        # so waking doesn't also trigger the widget under the finger.
        if self._dimmed:
            self.wake()
            return True
        self._reset_idle_timer()
        return False

    # --- action rail ---------------------------------------------------
    def refresh_rail(self):
        # WEIGH is always live.  ZERO is enabled whenever an empty-ish pot is on
        # the scale -- i.e. the reading is within ZERO_GUARD_G of the stored
        # tare, so not a potful of coffee and not an empty scale.  There is no
        # lower deadband: nulling out small residual error is exactly what the
        # tare is for, so even a reading right at the current tare stays
        # zeroable.  Off the scale or full of coffee, the button dims and the
        # left-side delta readout says why.  Mock keeps it live for the demo.
        self._enable(self._weigh_btn, True)
        self._enable(self._zero_btn, self.mock or self._zero_useful())

    def _zero_useful(self):
        # HomeScreen.__init__ ticks (-> refresh_rail) before self.home exists,
        # so tolerate a missing home / first reading.
        home = getattr(self, "home", None)
        if home is None:
            return False
        delta = abs(getattr(home, "_last_grams", 0.0) - self.settings["pot_tare_g"])
        return delta <= self.ZERO_GUARD_G

    @staticmethod
    def _enable(btn, on):
        btn.disabled = not on
        btn.opacity = 1.0 if on else 0.35

    # Guard band (grams) around the current tare within which ZERO will accept
    # the reading as "an empty pot".  Wide enough for a swapped carafe or a bit
    # of residue, narrow enough to reject a pot with coffee still in it.
    ZERO_GUARD_G = 150

    def _rail_zero(self):
        # Zero the empty pot: refuse unless the scale reading is close to the
        # current tare (an actually-empty carafe), so we never tare away a
        # potful of coffee.  On success, brewsource.zero() writes the new tare
        # and resets to idle.
        self._go("home")
        grams = getattr(self.home, "_last_grams", 0.0)
        tare = self.settings["pot_tare_g"]
        if not self.mock and abs(grams - tare) > self.ZERO_GUARD_G:
            self.home._flash("Put the EMPTY pot on the scale to zero")
            return
        self.source.zero()
        self.home.tick()

    def _go(self, name):
        self.sm.transition.direction = "right" if name == "home" else "left"
        self.sm.current = name

    def _on_key_down(self, _w, key, *_a):
        if key in (113, 27):  # q / Escape
            self.stop()
            return True
        return False


def parse_args(argv):
    p = argparse.ArgumentParser(description="brewcop touchscreen app")
    p.add_argument(
        "--mock", action="store_true", help="use canned states, no scale hardware"
    )
    p.add_argument(
        "--windowed", action="store_true", help="run in a window (dev), not fullscreen"
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    if not args.windowed:
        Window.fullscreen = "auto"
    BrewcopApp(mock=args.mock).run()

# vim: tabstop=4 shiftwidth=4 expandtab
