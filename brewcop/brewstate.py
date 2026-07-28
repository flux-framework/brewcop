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
Persist the brew state (Brains.snapshot()) across reboots.

ready_time is absolute wall-clock, so saving it and restoring on boot yields
the true coffee age even if the pot aged (or went stale) while brewcop was
powered off -- which is the only situation in normal use that would
otherwise lose the age (ready_time is otherwise just in RAM).

Stored as a small JSON file on the writable partition (same place as the
user settings; distinct file).  Absent/corrupt -> None (start clean).  Saved
only when the durable state actually changes, so writes are rare (kind to
the SD/f2fs partition and the read-only-root design).

Caveat: the Pi has no battery-backed RTC, so right after a power loss the
clock is wrong until NTP syncs (fast on wired chaosnet); the restored age is
briefly off until then.  And if the pot was replaced while powered off, the
restored age is stale -- but that fails safe (good coffee shown as old, not
the reverse) and self-corrects at the next observed brew or clean.
"""

import json
import os


def state_path():
    """Path to the brew-state JSON (BREWCOP_STATE, else beside settings)."""
    p = os.environ.get("BREWCOP_STATE")
    if p:
        return p
    base = os.environ.get("BREWCOP_CONFIG")
    if base:
        return os.path.join(os.path.dirname(base) or ".", "brewstate.json")
    return os.path.expanduser("~/.config/brewcop/brewstate.json")


def load(path=None):
    """Return the saved snapshot dict, or None if absent/unreadable."""
    p = path or state_path()
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(snapshot, path=None):
    """Write the snapshot dict.  Returns True on success, False on failure."""
    p = path or state_path()
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w") as f:
            json.dump(snapshot, f, indent=2)
        return True
    except OSError:
        return False


# vim: tabstop=4 shiftwidth=4 expandtab
