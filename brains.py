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
Brew state machine: interpret a series of scale weights as brew activity.

Extracted verbatim (logic-wise) from the original brewcop.py Brains class so
it is importable and testable.  The DETECTION LOGIC is intentionally
unchanged -- it is known to be over-eager (any upward blip over the 30 s
window counts as "brewing", so scale jitter or setting the pot back after a
pour can trip it; this caused a Slack notification storm).  It is preserved
as-is here, with characterization tests documenting the current behavior, so
a later redesign can be tuned against real weight traces captured with
test/scale_probe.py rather than guesswork.

Two things were decoupled from the original (plumbing, not logic):
  - The Slack POST is removed.  store() instead RETURNS an event string
    ("ready") on the brewing->ready transition; the caller decides whether
    to notify.  This is the storm-prone trigger, so gating it belongs with
    the app (and stays off until the detector is retuned).
  - The urwid display property is removed.  Brains exposes `state` and
    `elapsed()`; potstate.derive() owns presentation and staleness.

States: unknown | brewing | ready | empty.
"""

import time
from collections import deque


class Brains:
    """
    Interpret contents-weight (grams, net of pot tare) as brew activity.

    The discriminator is the RATE of weight change, which cleanly separates
    the two things that both "raise the weight":

      - A real brew: the pot sits on the scale at ~empty and coffee drips in,
        so the weight rises GRADUALLY (~1.25 L over ~5-6 min, a few g/s).
        This is the only thing that makes coffee "fresh".
      - A placement: the pot returns from being carried around, or something
        is set on the scale -- the weight JUMPS hundreds of g in one sample.
        Not a brew; the coffee (if any) is whatever it already was.

    Coffee AGE is wall-clock time since the last brew completed (ready_time).
    It ticks continuously -- including while the pot is off the scale being
    passed around -- so a pot returning from a meeting reads its true age
    (and may already be stale).  ready_time is reset ONLY by a gradual brew
    that then settles; every other change (removal, return heavier or
    lighter, pouring, a cup tipped back in) preserves it.

    Rate thresholds are provisional constants (grams/second), to be tuned
    against real weight traces captured with test/scale_probe.py.

    store() returns "ready" on the brewing->settled transition (the point to
    notify); state is empty|brewing|ready|present|unknown, where "present"
    means coffee is on the scale but we never observed it brew (cold start,
    or non-coffee weight) so its age is unknown -- distinct from "ready",
    which is only reachable by watching a brew.  elapsed() gives the value
    the UI wants per state (brew duration while brewing, coffee age while
    ready).
    """

    # Provisional rate thresholds (grams/second) -- tune with real traces.
    STABLE_RATE_GPS = 1.0  # |rate| below this: weight is stable
    BREW_RATE_MAX_GPS = 30.0  # rise faster than this: a placement, not a brew
    RATE_WINDOW_S = 4.0  # window over which rate is estimated

    # A brew only counts as complete when the settled level reaches its target
    # (full or half pot), within this margin.  The margin must cover the water
    # the grounds absorb -- a "full" brew yields somewhat less in the carafe.
    # Provisional; tune against a real brew trace.
    BREW_TARGET_MARGIN_G = 150

    def __init__(self, tick_period=1, empty_thresh=0, now=time.time):
        # tick_period is accepted for API compatibility but unused: rate is
        # computed from real timestamps over RATE_WINDOW_S, not sample counts.
        self.pot_empty_thresh_g = empty_thresh
        self._now = now  # injectable clock for testing
        self._samples = deque()  # (t, net) within RATE_WINDOW_S
        self.state = "unknown"
        self.ready_time = None  # wall-clock when current coffee became ready
        self.clean_time = None  # wall-clock when the pot was last cleaned
        self.timestamp = 0  # when the current state was entered
        self._target_g = None  # expected finished-brew level (set per store)

    def store(self, net, target_g=None):
        """
        Record a contents-weight sample (grams, net of tare) and update state.
        Returns "ready" on a brewing->settled transition, else None.

        target_g is the expected finished-brew level (full or half pot, in
        grams).  A settle below target_g - BREW_TARGET_MARGIN_G is treated as
        an incomplete brew, NOT a completed one -- so a dribble on the scale
        no longer flashes "fresh coffee".  None disables the check (accept any
        settled brew, the old behavior).
        """
        t = self._now()
        self._samples.append((t, net))
        while self._samples and t - self._samples[0][0] > self.RATE_WINDOW_S:
            self._samples.popleft()
        self._target_g = target_g
        return self._update(t, net)

    def _rate(self):
        """Estimated rate of change (g/s) over the sample window."""
        if len(self._samples) < 2:
            return 0.0
        t0, n0 = self._samples[0]
        t1, n1 = self._samples[-1]
        if t1 <= t0:
            return 0.0
        return (n1 - n0) / (t1 - t0)

    def _update(self, t, net):
        prev = self.state
        rate = self._rate()

        if net <= self.pot_empty_thresh_g:
            # No coffee present (empty pot, or pot absent -> net goes very
            # negative).  Do NOT reset ready_time: coffee on a walkabout keeps
            # aging, and an emptied pot's age is moot until the next brew.
            self._set_state(t, "empty")
            return None

        # Coffee is present.
        if self.STABLE_RATE_GPS < rate <= self.BREW_RATE_MAX_GPS:
            # Gradual, sustained rise -> a brew in progress.
            self._set_state(t, "brewing")
            return None

        # Otherwise settled: stable, declining (pouring), or a step jump
        # (placement / return / pour-back).  None of these is brewing.
        event = None
        if prev == "brewing" and self._reached_target(net):
            # A brew just finished on the scale AND reached the target level.
            # Accept it as fresh only if the pot was cleaned since the last
            # batch; otherwise it's a brew into a still-dirty pot -- refuse to
            # reset the age clock, so the pot stays flagged dirty (you must
            # clean it, i.e. dump and rebrew).
            if self._cleaned_since_last_brew():
                self.ready_time = t
                event = "ready"
            self._set_state(t, "ready")
        elif self.ready_time is not None:
            # Coffee present with an age from an earlier brew (e.g. the pot
            # returning after being carried around) -> keep it.
            self._set_state(t, "ready")
        else:
            # Coffee present but never observed brewing (cold start, or
            # non-coffee weight).  Age unknown -- do NOT claim ready/notify.
            self._set_state(t, "present")
        return event

    def _set_state(self, t, s):
        if self.state != s:
            self.state = s
            self.timestamp = t

    def _reached_target(self, net):
        # A completed brew must reach the target level (within margin).  No
        # target set -> accept any settled brew (check disabled).
        if self._target_g is None:
            return True
        return net >= self._target_g - self.BREW_TARGET_MARGIN_G

    def _cleaned_since_last_brew(self):
        # True if there's no batch yet, or the pot was cleaned after the last
        # accepted brew.  Gates whether a new brew is accepted as fresh.
        if self.ready_time is None:
            return True
        return self.clean_time is not None and self.clean_time > self.ready_time

    def is_stale(self, stale_s, now=None):
        """True if the current batch has aged past the stale threshold."""
        if self.ready_time is None:
            return False
        if now is None:
            now = self._now()
        return (now - self.ready_time) >= stale_s

    def is_dirty(self, stale_s, now=None):
        """
        True if the pot needs cleaning: a batch has gone stale and the pot
        has not been cleaned since that batch brewed.  This is what drives the
        biohazard, and it survives a rebrew (a dirty rebrew is rejected, so
        ready_time still points at the stale batch).
        """
        return self.is_stale(stale_s, now) and not self._cleaned_since_last_brew()

    def clean(self, now=None):
        """Record that the pot was cleaned (clears the dirty flag)."""
        if now is None:
            now = self._now()
        self.clean_time = now

    def elapsed(self, now=None):
        """
        Seconds the UI should show for the current state:
          ready   -> coffee age (now - ready_time), ticks continuously
          other   -> time in the current state (e.g. brew duration)
        """
        if now is None:
            now = self._now()
        if self.state == "ready" and self.ready_time is not None:
            return now - self.ready_time
        return now - self.timestamp

    # --- persistence --------------------------------------------------
    # ready_time is absolute wall-clock, so persisting it across a reboot
    # restores the *true* coffee age (now - ready_time) even if the pot aged
    # while brewcop was powered off.  The transient weight window is not
    # persisted (it rebuilds within RATE_WINDOW_S); only the durable brew
    # facts are.  IO lives in the caller (brewsource), not here.

    def snapshot(self):
        """Return the durable brew state as a plain dict (JSON-friendly)."""
        return {
            "state": self.state,
            "ready_time": self.ready_time,
            "clean_time": self.clean_time,
            "timestamp": self.timestamp,
        }

    def restore(self, snap):
        """Restore durable brew state from a snapshot() dict (best effort)."""
        if not snap:
            return
        self.state = snap.get("state", self.state)
        self.ready_time = snap.get("ready_time", self.ready_time)
        self.clean_time = snap.get("clean_time", self.clean_time)
        self.timestamp = snap.get("timestamp", self.timestamp)


# vim: tabstop=4 shiftwidth=4 expandtab
