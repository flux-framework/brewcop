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
User settings: the runtime-tweakable preferences edited on the Settings
screen (stale timeout, pot tare/capacity, empty threshold, Slack on/off,
backlight dimming).  Distinct from machineconfig (install-time facts on the
read-only root): these change at runtime and must survive reboots, so they
live in a writable JSON, saved only on explicit Save.

Under read-only root + overlayfs, ordinary writes are discarded, so the
deployment points BREWCOP_CONFIG at a file on a small dedicated writable
partition (e.g. /var/lib/brewcop/config.json).  Absent/corrupt file ->
defaults.  (Factored out of the demo mockup's inline Config class.)

Config is the single source of truth: everything derived from these values
is computed, never hardcoded (see potstate, the dosing hint, etc.).
"""

import json
import os


DEFAULTS = {
    "slack_enabled": False,  # announce ready pots to Slack (off until retuned)
    "stale_hours": 4.0,  # declare coffee stale after N hours
    "empty_thresh_g": 50,  # below this net weight, pot is "empty-ish"
    "pot_tare_g": 795,  # empty Technivorm insulated carafe
    "pot_capacity_ml": 1250,  # full pot (1 g per mL water)
    "dim_timeout_s": 120,  # dim backlight after N s of no touch (0 = never)
    "dim_level": 0.15,  # dimmed brightness fraction (0..1)
    "wake_on_event": True,  # brighten on ready/stale state change
}


def config_path():
    """Path to the writable settings JSON (BREWCOP_CONFIG or a user default)."""
    p = os.environ.get("BREWCOP_CONFIG")
    if p:
        return p
    return os.path.expanduser("~/.config/brewcop/config.json")


class UserSettings:
    """JSON-backed settings with defaults.  Persists only on save()."""

    def __init__(self, path=None):
        self._path = path or config_path()
        self.values = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self._path) as f:
                data = json.load(f)
            for k in DEFAULTS:
                if k in data:
                    self.values[k] = data[k]
        except (OSError, ValueError):
            pass  # missing/corrupt -> keep defaults

    def save(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self.values, f, indent=2)

    def __getitem__(self, k):
        return self.values[k]

    def __setitem__(self, k, v):
        self.values[k] = v


# vim: tabstop=4 shiftwidth=4 expandtab
