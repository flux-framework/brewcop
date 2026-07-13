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
Brew state machine -- explicit, user-driven.

Inferring the brew cycle from scale weight alone was never reliable (a
dribble or a placed object looked like a brew; the rate thresholds were
guesses).  Every such bug came from trying to infer the user's *intent* from
weight.  So intent is now explicit -- the user drives the transitions -- and
the scale is used only for what it is reliable at: measuring.

States and transitions:

  idle ── start_brew(target_g) ──▶ brewing
                                     │  weight >= target_g - margin  (auto)
                                     │  mark_ready()                 (manual)
                                     ▼
                                   ready ── clean_up() ──▶ idle

- idle:    no active batch.  The weight still tells the UI whether a pot is
           sitting there, but nothing is claimed about freshness.
- brewing: armed by BREW; watching the weight climb to the dialed target.
           Completes automatically when the level reaches the target (within
           a margin that must cover water the grounds absorb), or manually
           via mark_ready() as a fallback if the margin is off.
- ready:   ready_time set; coffee ages by wall-clock (ticks even while the
           pot is carried around -- the state is user-driven, so no inference
           is needed to keep it).  is_stale() drives the biohazard once the
           batch ages past the stale timeout.

clean_up() is the ONLY exit from ready, and brewing is only reachable from
idle -- so "you must clean up before brewing again" is enforced by the
workflow, with no dirty-tracking heuristics.

store() returns "ready" on the transition into ready (the point to notify).
elapsed() gives coffee age while ready, else time in the current state.
"""

import time


class Brains:
    """Explicit idle/brewing/ready state machine over contents weight."""

    # A brew completes when the settled level reaches its target within this
    # margin.  The margin must cover the water the grounds absorb (a "full"
    # brew yields somewhat less in the carafe).  Provisional; tune against a
    # real brew trace.  The manual mark_ready() fallback covers a bad margin.
    BREW_TARGET_MARGIN_G = 150

    def __init__(self, tick_period=1, empty_thresh=0, now=time.time):
        # tick_period accepted for API compatibility; unused now.
        self.pot_empty_thresh_g = empty_thresh
        self._now = now  # injectable clock for testing
        self.state = "idle"
        self.ready_time = None  # wall-clock when the batch became ready
        self.target_g = None  # dialed brew target while brewing
        self.timestamp = 0  # when the current state was entered
        self._net = 0.0  # last contents-weight sample

    # --- user-driven transitions --------------------------------------
    def start_brew(self, target_g):
        """BREW pressed: arm brewing toward target_g grams of contents."""
        self.target_g = target_g
        self.ready_time = None
        self._set_state("brewing")

    def mark_ready(self):
        """Manual completion fallback (brew finished but under the margin).
        Returns "ready" if it caused the transition, else None."""
        if self.state == "brewing":
            return self._become_ready()
        return None

    def clean_up(self):
        """CLEAN UP pressed: batch dealt with, return to idle."""
        self.ready_time = None
        self.target_g = None
        self._set_state("idle")

    # --- measurement ---------------------------------------------------
    def store(self, net):
        """
        Record a contents-weight sample (grams, net of tare).  While brewing,
        auto-completes to ready when the level reaches the target.  Returns
        "ready" on that transition, else None.
        """
        self._net = net
        if self.state == "brewing" and self.target_g is not None:
            if net >= self.target_g - self.BREW_TARGET_MARGIN_G:
                return self._become_ready()
        return None

    def _become_ready(self):
        self.ready_time = self._now()
        self._set_state("ready")
        return "ready"

    def _set_state(self, s):
        if self.state != s:
            self.state = s
            self.timestamp = self._now()

    # --- derived --------------------------------------------------------
    def is_stale(self, stale_s, now=None):
        """True if a ready batch has aged past the stale threshold."""
        if self.state != "ready" or self.ready_time is None:
            return False
        if now is None:
            now = self._now()
        return (now - self.ready_time) >= stale_s

    def elapsed(self, now=None):
        """
        Seconds the UI shows for the current state:
          ready -> coffee age (now - ready_time), ticks continuously
          else  -> time in the current state (e.g. brew duration)
        """
        if now is None:
            now = self._now()
        if self.state == "ready" and self.ready_time is not None:
            return now - self.ready_time
        return now - self.timestamp

    # --- persistence ---------------------------------------------------
    # ready_time is absolute wall-clock, so persisting it restores the *true*
    # coffee age across a reboot (even if the pot aged while powered off).
    # The active state and target are persisted too, so a brew or ready pot
    # resumes correctly.

    def snapshot(self):
        return {
            "state": self.state,
            "ready_time": self.ready_time,
            "target_g": self.target_g,
            "timestamp": self.timestamp,
        }

    def restore(self, snap):
        if not snap:
            return
        self.state = snap.get("state", self.state)
        self.ready_time = snap.get("ready_time", self.ready_time)
        self.target_g = snap.get("target_g", self.target_g)
        self.timestamp = snap.get("timestamp", self.timestamp)


# vim: tabstop=4 shiftwidth=4 expandtab
