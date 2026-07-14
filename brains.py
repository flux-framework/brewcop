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
                                     │  contents >= target_g  (finished level)
                                     ▼
                                   ready ── clean_up() ──▶ idle

- idle:    no active batch.  The weight still tells the UI whether a pot is
           sitting there, but nothing is claimed about freshness.
- brewing: armed by BREW; watching the weight climb to the dialed target.
           target_g is the FINISHED-pot level, so completion is an exact
           match (within scale noise), no absorption margin.  If a brew
           stalls short, the user dials the target down to complete it.
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

from scale import POT_TOLERANCE_G


class Brains:
    """Explicit idle/brewing/ready state machine over contents weight."""

    def __init__(self, tick_period=1, empty_thresh=0, now=time.time):
        # tick_period accepted for API compatibility; unused now.
        self.pot_empty_thresh_g = empty_thresh
        self._now = now  # injectable clock for testing
        self.state = "idle"
        self.ready_time = None  # wall-clock when the batch became ready
        self.target_g = None  # dialed brew target while brewing
        self.timestamp = 0  # when the current state was entered
        self._net = 0.0  # last contents-weight sample
        # "Needs clean" latch: set once a ready batch ages past the stale
        # timeout, and NOT cleared by a subsequent brew -- only by clean_up().
        # It is what keeps the biohazard on-screen as a "press CLEAN" reminder
        # even into the next brew (the meatbags decide when to deal with it).
        self.dirty = False

    # --- user-driven transitions --------------------------------------
    def start_brew(self, target_g):
        """BREW pressed: arm brewing toward target_g grams of contents."""
        self.target_g = target_g
        self.ready_time = None
        self._set_state("brewing")

    def clean_up(self):
        """CLEAN UP pressed: batch dealt with, return to idle and clear the
        needs-clean latch (the ONLY thing that clears it)."""
        self.ready_time = None
        self.target_g = None
        self.dirty = False
        self._set_state("idle")

    # --- measurement ---------------------------------------------------
    def store(self, net):
        """
        Record a contents-weight sample (grams, net of tare).  While brewing,
        completes to ready when the level reaches the dialed target (which is
        the finished-pot level, so this is an exact match within scale noise
        -- no absorption margin).  Returns "ready" on that transition, else
        None.  If a brew stalls short, the user dials the target down to the
        level actually reached, which completes it (dial-to-complete).
        """
        self._net = net
        if self.state == "brewing" and self.target_g is not None:
            if net >= self.target_g - POT_TOLERANCE_G:
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
        """True if the *current* ready batch has aged past the stale threshold.
        A pure freshness query (drives drawing the pot empty); the persistent
        biohazard reminder uses the `dirty` latch, not this."""
        if self.state != "ready" or self.ready_time is None:
            return False
        if now is None:
            now = self._now()
        return (now - self.ready_time) >= stale_s

    def update_dirty(self, stale_s, now=None):
        """Latch the needs-clean flag once the current ready batch goes stale.
        Called each poll.  Once set, it stays set through a new brew and only
        clean_up() clears it.  Returns the current latch value."""
        if self.is_stale(stale_s, now=now):
            self.dirty = True
        return self.dirty

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
            "dirty": self.dirty,
        }

    def restore(self, snap):
        if not snap:
            return
        self.state = snap.get("state", self.state)
        self.ready_time = snap.get("ready_time", self.ready_time)
        self.target_g = snap.get("target_g", self.target_g)
        self.timestamp = snap.get("timestamp", self.timestamp)
        self.dirty = snap.get("dirty", self.dirty)


# vim: tabstop=4 shiftwidth=4 expandtab
