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
Map real scale + brew-state data to the touchscreen's pot-state.

This is the pure decision layer between the hardware (scale weight, Brains
state machine) and the Kivy carafe UI.  It is deliberately free of Kivy and
serial imports so it can be unit-tested headlessly -- which matters because
this is exactly the logic that was historically dodgy (see the notification
storm) and needs to be gotten right.

The UI's home screen consumes a small record describing what to draw:
  key      one of: no_pot | empty | present | brewing | ready
  text     human-readable status line
  fill     carafe fill fraction 0..1 (ignored by UI when key is no_pot/empty)
  age_s    raw age seconds while ready (drives the carafe count-up clock)

Config is the single source of truth (MODERNIZATION.md): tare and capacity
come from config, never hardcoded here.  The empty threshold is a fixed module
constant (EMPTY_THRESH_G): with per-brew zeroing the pot tare is accurate, so a
small fixed band is enough to call a pot "empty" and there is nothing left to
tune on the Settings screen.  Freshness is no longer judged here: a ready batch
just carries a running age clock (reset by hand via the RESET button); the
human reads the clock and decides.
"""

from scale import POT_TOLERANCE_G


# Below this many grams of net contents a pot reads "empty".  A fixed band (no
# longer a user setting): per-brew zeroing keeps the tare accurate, so this
# only has to absorb a few grams of dried-on residue / scale drift.
EMPTY_THRESH_G = 30


class PotState:
    """Plain result record (no UI types)."""

    def __init__(
        self,
        key,
        text,
        fill=0.0,
        age="",
        age_s=None,
    ):
        self.key = key
        self.text = text
        self.fill = fill
        self.age = age  # compact elapsed string (for the carafe age clock)
        self.age_s = age_s  # raw elapsed seconds (None unless ready), so the
        # UI can format the running HH:MM:SS count-up clock

    def __eq__(self, other):
        return isinstance(other, PotState) and (
            self.key,
            self.text,
            round(self.fill, 4),
        ) == (other.key, other.text, round(other.fill, 4))

    def __repr__(self):
        return "PotState(key={!r}, fill={:.3f}, text={!r})".format(
            self.key, self.fill, self.text
        )


def net_contents_g(raw_weight_g, pot_tare_g):
    """
    Grams of *contents* (coffee) given the raw scale weight and the empty-pot
    tare.  Applies the pot-tare tolerance: a reading within POT_TOLERANCE_G of
    the tare counts as exactly tare (0 contents), absorbing real-world slop in
    the carafe's measured weight.  Returns a value that may be negative when
    no pot is present (raw weight well below tare).
    """
    net = raw_weight_g - pot_tare_g
    if -POT_TOLERANCE_G < net < POT_TOLERANCE_G:
        return 0.0
    return net


def fmt_elapsed(seconds):
    """Compact human elapsed time, e.g. '12 min', '2h 10m', '1d 3h'."""
    s = int(max(0, seconds))
    if s < 60:
        return "{}s".format(s)
    m = s // 60
    if m < 60:
        return "{} min".format(m)
    h = m // 60
    m = m % 60
    if h < 24:
        return "{}h {}m".format(h, m)
    d = h // 24
    h = h % 24
    return "{}d {}h".format(d, h)


def derive(
    raw_weight_g,
    weight_is_valid,
    brew_state,
    elapsed_s,
    config,
):
    """
    Compute the PotState to display.

    Parameters:
      raw_weight_g      latest raw scale reading in grams (pre-tare)
      weight_is_valid   whether the last poll produced a valid weight
      brew_state        Brains state: idle|brewing|ready
      elapsed_s         coffee age (ready) or time-in-state, seconds
      config            pot_tare_g, pot_capacity_ml

    A ready batch carries a running age clock (age_s) that only the RESET
    button clears -- staleness is not judged here.  The ready check precedes
    the empty check on purpose: pouring the coffee out does NOT clear the
    clock, so an emptied-but-not-reset carafe keeps counting up.

    Returns a PotState.  Pure function -- no side effects.
    """
    tare = config["pot_tare_g"]
    capacity = config["pot_capacity_ml"]  # 1 g per mL

    # If we don't have a valid weight, don't invent one.
    if not weight_is_valid:
        return PotState("no_pot", "Scale reading unavailable", 0.0)

    net = net_contents_g(raw_weight_g, tare)

    # Below the pot tare: nothing (or nothing pot-like) on the scale.  The
    # clock can't draw over an absent carafe; it resumes when the pot returns,
    # since Brains stays ready.  (Home shows the raw weight separately.)
    if net < 0:
        return PotState("no_pot", "No pot on scale", 0.0)

    fill = max(0.0, min(1.0, net / capacity)) if capacity > 0 else 0.0
    litres = net / 1000.0
    elapsed_txt = fmt_elapsed(elapsed_s)

    # Brewing: the boiler is running / the pour is finishing.  Show the
    # climbing fill.
    if brew_state == "brewing":
        return PotState(
            "brewing",
            "Brewing - {}".format(elapsed_txt),
            fill,
        )

    # ready: a completed batch, running its age clock.  Returned even when the
    # pot has been emptied (net below the empty band) -- only RESET stops the
    # clock, so an emptied-but-not-reset carafe keeps counting up.
    if brew_state == "ready":
        return PotState(
            "ready",
            "Coffee: ~{:.2f} L - {}".format(litres, elapsed_txt),
            fill,
            age=elapsed_txt,
            age_s=elapsed_s,
        )

    # Pot present but effectively empty (idle, no batch).
    if net <= EMPTY_THRESH_G:
        return PotState("empty", "Empty pot", fill)

    # Coffee present but no active batch (idle): a pot is sitting on the scale
    # that we never saw brew (e.g. carried over from before startup) -- show
    # the level, but claim no age (no clock).
    return PotState(
        "present",
        "Coffee: ~{:.2f} L".format(litres),
        fill,
    )


# vim: tabstop=4 shiftwidth=4 expandtab
