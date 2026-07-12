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
  key      one of: no_pot | empty | fresh | aging | stale | brewing
  text     human-readable status line
  fill     carafe fill fraction 0..1 (ignored by UI when key is no_pot/empty)
  expired  True -> draw empty + blinking biohazard (stale, latched)

Config is the single source of truth (MODERNIZATION.md): tare, capacity,
empty threshold, tolerance, and stale timeout all come from config, never
hardcoded here.
"""

from scale import POT_TOLERANCE_G


# Fraction thresholds (of stale timeout) at which fresh coffee starts to be
# called "aging" in the status text.  Purely cosmetic; does not gate the
# biohazard, which is driven by the stale timeout.
AGING_FRACTION = 0.5


class PotState:
    """Plain result record (no UI types)."""

    def __init__(self, key, text, fill=0.0, expired=False):
        self.key = key
        self.text = text
        self.fill = fill
        self.expired = expired

    def __eq__(self, other):
        return isinstance(other, PotState) and (
            self.key,
            self.text,
            round(self.fill, 4),
            self.expired,
        ) == (other.key, other.text, round(other.fill, 4), other.expired)

    def __repr__(self):
        return "PotState(key={!r}, fill={:.3f}, expired={}, text={!r})".format(
            self.key, self.fill, self.expired, self.text
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
    dirty=False,
):
    """
    Compute the PotState to display.

    Parameters:
      raw_weight_g      latest raw scale reading in grams (pre-tare)
      weight_is_valid   whether the last poll produced a valid weight
      brew_state        Brains state: unknown|brewing|ready|present|empty
      elapsed_s         coffee age (ready) or time-in-state, seconds
      config            pot_tare_g, pot_capacity_ml, empty_thresh_g, stale_hours
      dirty             pot needs cleaning: a batch went stale and the pot
                        hasn't been cleaned since (from Brains.is_dirty).  The
                        biohazard is driven by THIS, not by elapsed, so it
                        persists across dumping/rebrew until CLEAN.

    Returns a PotState.  Pure function -- no side effects.
    """
    tare = config["pot_tare_g"]
    capacity = config["pot_capacity_ml"]  # 1 g per mL
    empty_thresh = config["empty_thresh_g"]
    stale_s = float(config["stale_hours"]) * 3600.0

    # If we don't have a valid weight, don't invent one.
    if not weight_is_valid:
        return PotState("no_pot", "Scale reading unavailable", 0.0, False)

    net = net_contents_g(raw_weight_g, tare)

    # Below the pot tare: nothing (or nothing pot-like) on the scale.  Show
    # no_pot even if the pot is "dirty" -- there's nothing to draw a hazard
    # over; the dirty flag persists in Brains and re-asserts when a pot
    # returns.  (Home shows the raw weight separately.)
    if net < 0:
        return PotState("no_pot", "No pot on scale", 0.0, False)

    fill = max(0.0, min(1.0, net / capacity)) if capacity > 0 else 0.0
    litres = net / 1000.0
    elapsed_txt = fmt_elapsed(elapsed_s)

    # Dirty pot (a batch went stale, not cleaned since).  Drives the
    # biohazard and survives dumping/rebrew.  Rendered like the old "stale"
    # (empty carafe + hazard), whether or not coffee is still present.
    if dirty:
        return PotState(
            "stale", "Stale coffee - please dump ({})".format(elapsed_txt), fill, True
        )

    # Pot present but effectively empty (and not dirty).
    if net <= empty_thresh:
        return PotState("empty", "Empty pot", fill, False)

    # Pot has coffee.
    if brew_state == "brewing":
        return PotState("brewing", "Brewing - {}".format(elapsed_txt), fill, False)

    # Coffee present but never observed brewing (cold start / non-coffee
    # weight): age unknown -- no freshness claim, no timer.
    if brew_state != "ready":
        return PotState(
            "present", "Coffee: ~{:.2f} L - age unknown".format(litres), fill, False
        )

    # ready and not dirty: fresh -> aging by the (known) age.
    if elapsed_s >= stale_s * AGING_FRACTION:
        return PotState(
            "aging",
            "Coffee: ~{:.2f} L - aging ({})".format(litres, elapsed_txt),
            fill,
            False,
        )

    return PotState(
        "fresh",
        "Coffee: ~{:.2f} L - fresh ({})".format(litres, elapsed_txt),
        fill,
        False,
    )


# vim: tabstop=4 shiftwidth=4 expandtab
