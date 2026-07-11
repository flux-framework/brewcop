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
Static UI mockup for the redesigned brewcop touchscreen.

This is a *visual/navigation* prototype -- no scale, no serial, no Slack.
Scale-derived values are faked.  Its purpose is to iterate on layout,
navigation, and feel on the real panel before wiring up behavior.

Screens (three total):
  Home     -- Flux logo (placeholder) + wordmark, a live Technivorm carafe
              showing coffee level/freshness (with a RIP tombstone when a
              pot expires but still holds coffee), a persistent pot-status
              line, and Weigh / Settings buttons.  Monitoring is ALWAYS ON
              in the background; Home is the brew view.  Tap the carafe to
              cycle faked states.
  Weigh    -- big live weight, g/oz units toggle, tare, dosing hint
  Settings -- Slack announcements on/off + touch sliders for tunable
              parameters, saved to a JSON config

There is deliberately no separate "Brew" screen: the live Home carafe is a
better brew view than a dedicated page, so monitoring simply runs always
and Home reflects it.  (This implies conservative brew-detection in the
real logic so a set-down mug does not trigger a false "coffee ready" Slack.)

Config persistence note: under read-only root + overlayfs, ordinary writes
are discarded on reboot.  The real deployment must place config.json on a
small dedicated writable (ideally f2fs) partition, e.g. /var/lib/brewcop,
written only on explicit Save.  For this mockup we write to
$BREWCOP_CONFIG or ~/.config/brewcop/config.json.

Run on the Pi touchscreen (python3-kivy installed):

    python3 ui_mockup.py

Press 'q' or Escape to quit.  Targets the Kivy 2.1.0 API (Bookworm).
"""

import json
import os

import kivy

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Rectangle, Line, Mesh
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image as ImageWidget
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.widget import Widget

kivy.require("2.1.0")

# Image assets live in ../images relative to this script (demo/), so the
# path resolves regardless of the current working directory.
IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "images")
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


# --- config persistence --------------------------------------------------
DEFAULTS = {
    "slack_enabled": True,  # announce ready pots to Slack
    "stale_hours": 4.0,  # declare coffee stale after N hours
    "empty_thresh_g": 50,  # below this net weight, pot is "empty-ish"
    "pot_tare_g": 795,  # empty Technivorm insulated carafe
    "pot_capacity_ml": 1250,  # full pot (1 g per mL water)
}

# Scale/pot weight tolerance: brewcop.py treats readings within +/-4 g of
# the pot tare as "at tare" (see "allow small variation in technivorm pot
# weight").  Surface this on weight settings so 1 g steps don't imply
# precision finer than the effective noise floor.
POT_TOLERANCE_G = 4


def config_path():
    p = os.environ.get("BREWCOP_CONFIG")
    if p:
        return p
    return os.path.expanduser("~/.config/brewcop/config.json")


class Config:
    """Tiny JSON-backed settings store with defaults.  Saves only on save()."""

    def __init__(self):
        self.values = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(config_path()) as f:
                data = json.load(f)
            for k in DEFAULTS:
                if k in data:
                    self.values[k] = data[k]
        except (OSError, ValueError):
            pass  # missing/corrupt -> keep defaults

    def save(self):
        path = config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.values, f, indent=2)

    def __getitem__(self, k):
        return self.values[k]

    def __setitem__(self, k, v):
        self.values[k] = v


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
        # Load the biohazard PNG once; tolerate it being absent (fall back to
        # no image -- the caption still conveys the warning).
        try:
            self._hazard_tex = CoreImage(BIOHAZARD_PNG).texture
        except Exception:
            self._hazard_tex = None
        self.bind(pos=self._redraw, size=self._redraw)

    # --- public state --------------------------------------------------
    def set_state(self, fill, coffee_rgba, expired):
        self._fill = max(0.0, min(1.0, fill))
        self._coffee = coffee_rgba
        self._expired = expired
        show_hazard = expired and self._fill > 0.02
        self._set_blinking(show_hazard)
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
        self.canvas.clear()
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

        with self.canvas:
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
                inset = dp(5)
                fh = (body_h - 2 * inset) * self._fill
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

        # Biohazard overlay: expired AND coffee still present (fill>0 means
        # there's old coffee to dump; an expired *empty* pot is just empty).
        # The carafe is drawn empty above, so the blinking symbol sits in an
        # empty steel body -- "gone bad, don't drink".
        if self._expired and self._fill > 0.02:
            body_cy = by + body_h * 0.42
            self._draw_hazard(cx, body_cy, min(top_w, base_w), body_h)

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
            with self.canvas:
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
POT_STATES = [
    ("no_pot", MUTED, "No pot on scale", -1.0, GREEN, False),
    ("empty", MUTED, "Empty pot", 0.02, GREEN, False),
    ("fresh", GREEN, "Coffee: ~0.94 L - fresh (12 min)", 0.75, GREEN, False),
    ("aging", AMBER, "Coffee: ~0.70 L - aging (2h 10m)", 0.56, AMBER, False),
    # stale: fill value is ignored (expired pots draw empty); the blinking
    # biohazard in an empty steel carafe conveys "gone bad".
    ("stale", AMBER, "Stale coffee - please dump (4h 20m)", 0.60, GREEN, True),
]


class HomeScreen(Screen):
    def __init__(self, go, **kwargs):
        super().__init__(**kwargs)
        self.go = go
        self._pot_i = 2  # start on a happy "fresh" state
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

        # carafe centerpiece (tap anywhere on it to cycle faked states)
        center = FloatLayout()
        self.carafe = CarafeWidget(size_hint=(None, None))

        def _place_carafe(*_a):
            # square-ish, centered, sized to the available center area
            side = min(center.width * 0.6, center.height * 0.82)
            self.carafe.size = (side, side * 1.05)
            self.carafe.pos = (
                center.x + (center.width - self.carafe.width) / 2,
                center.y + (center.height - self.carafe.height) / 2,
            )

        center.bind(pos=_place_carafe, size=_place_carafe)
        center.add_widget(self.carafe)
        root.add_widget(center)

        # persistent pot-status line
        self.status = Label(
            text="", font_size=sp(20), bold=True, size_hint_y=None, height=dp(40)
        )
        root.add_widget(self.status)

        # a full-width invisible tap strip to cycle mock states (mockup only)
        cycle = FlatButton(
            text="(tap to cycle mock states)",
            bg=BG,
            fg=MUTED,
            font_size=sp(12),
            size_hint_y=None,
            height=dp(24),
        )
        cycle.bind(on_release=lambda *_a: self._cycle())
        root.add_widget(cycle)

        # Bottom action button.  Normally "WEIGH BEANS" (the only interactive
        # mode, since monitoring is always-on).  When a pot is stale, it
        # becomes "MARK CLEANED": the biohazard latches until a human
        # confirms the pot was dealt with -- so a top-up (weight rising)
        # can't silently clear a contaminated pot.
        self.action = FlatButton(
            text="WEIGH BEANS",
            bg=ACCENT,
            fg=(1, 1, 1, 1),
            font_size=sp(26),
            bold=True,
            size_hint_y=None,
            height=dp(96),
        )
        self.action.bind(on_release=lambda *_a: self._action())
        root.add_widget(self.action)

        self.add_widget(root)
        self._render_status()

    def _cycle(self):
        self._pot_i = (self._pot_i + 1) % len(POT_STATES)
        self._render_status()

    def _action(self):
        # In the stale state the button acknowledges cleaning; otherwise it
        # opens the bean scale.
        _key = POT_STATES[self._pot_i][0]
        if _key == "stale":
            self._mark_cleaned()
        else:
            self.go("weigh")

    def _mark_cleaned(self):
        # Human confirmed the pot was emptied/cleaned: clear the latched
        # hazard.  In the mockup we jump to the "empty pot" state.
        for i, s in enumerate(POT_STATES):
            if s[0] == "empty":
                self._pot_i = i
                break
        self._render_status()

    def _render_status(self):
        _key, color, text, fill, coffee, expired = POT_STATES[self._pot_i]
        self.status.text = text
        self.status.color = color
        if fill < 0:
            # No carafe on the scale: show nothing at all (blank centerpiece)
            # rather than a ghosted/handle-less shape.
            self.carafe.set_state(0.0, coffee, False)
            self.carafe.opacity = 0.0
        else:
            # A pot is present (including empty): draw the carafe.
            self.carafe.opacity = 1.0
            self.carafe.set_state(fill, coffee, expired)
        # Repurpose the bottom button when stale.
        if _key == "stale":
            self.action.text = "MARK CLEANED"
            self.action._bg_rgba = HAZARD
            self.action._bg_color.rgba = HAZARD
            self.action.color = (0.1, 0.1, 0.1, 1)
        else:
            self.action.text = "WEIGH BEANS"
            self.action._bg_rgba = ACCENT
            self.action._bg_color.rgba = ACCENT
            self.action.color = (1, 1, 1, 1)


# --- Weigh ---------------------------------------------------------------
class WeighScreen(Screen):
    GRAMS = 128.0  # faked steady reading

    def __init__(self, go, config, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.units = "g"
        self._offset = 0.0
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
        # Recompute each time the screen is shown, in case pot capacity was
        # just changed in Settings.
        self._refresh_hint()

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
        self._offset = self.GRAMS
        self._refresh()

    def _refresh(self):
        net = self.GRAMS - self._offset
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
        tol = "+/- {} g".format(POT_TOLERANCE_G)
        rows.add_widget(
            StepperRow(
                "Empty pot threshold",
                config,
                "empty_thresh_g",
                0,
                200,
                1,
                lambda v: "{:.0f} g".format(v),
                note=tol,
            )
        )
        rows.add_widget(
            StepperRow(
                "Pot tare (empty carafe)",
                config,
                "pot_tare_g",
                700,
                900,
                1,
                lambda v: "{:.0f} g".format(v),
                note=tol,
            )
        )
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
            self.save_msg.text = "Saved to {}".format(config_path())
        except OSError as e:
            self.save_msg.color = RED
            self.save_msg.text = "Save failed: {}".format(e)


class BrewcopApp(App):
    title = "brewcop ui mockup"

    def build(self):
        self.config = Config()
        Window.clearcolor = BG
        if Window is not None:
            Window.bind(on_key_down=self._on_key_down)

        self.sm = ScreenManager(transition=SlideTransition(duration=0.2))
        go = self._go
        self.sm.add_widget(HomeScreen(go, name="home"))
        self.sm.add_widget(WeighScreen(go, self.config, name="weigh"))
        self.sm.add_widget(SettingsScreen(go, self.config, name="settings"))
        return self.sm

    def _go(self, name):
        self.sm.transition.direction = "right" if name == "home" else "left"
        self.sm.current = name

    def _on_key_down(self, _w, key, *_a):
        if key in (113, 27):  # q / Escape
            self.stop()
            return True
        return False


if __name__ == "__main__":
    BrewcopApp().run()

# vim: tabstop=4 shiftwidth=4 expandtab
