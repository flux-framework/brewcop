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
Machine / deployment config (read-only TOML).

This is the *install-time* config -- facts set once when the unit is
provisioned and never written at runtime: the serial port path, the Slack
webhook URL (a secret), whether Slack is enabled, and the location string.
Because it is never written while running, it lives fine on the read-only
root (unlike the user-tweakable settings, which need a writable partition).

Distinct from the user settings (stale timeout, pot tare, etc.) edited via
the touchscreen Settings screen -- see MODERNIZATION.md, "Two configs".

Load precedence (later overrides earlier):
  1. built-in DEFAULTS
  2. TOML file (explicit path, else $BREWCOP_MACHINE_CONFIG, else
     /etc/brewcop/config.toml)  -- absent file is fine
  3. environment fallbacks (SLACK_WEBHOOK_URL) -- dev convenience

Read via stdlib tomllib (Python 3.11+), so no extra apt dependency.
"""

import os
import tomllib


DEFAULT_PATH = "/etc/brewcop/config.toml"

DEFAULTS = {
    # Serial device for the scale. "auto" -> autodetect a USB adapter.
    "serial_port": "auto",
    # Slack notification (OFF by default -- do not surprise anyone until the
    # brew-detection logic is validated against real data).
    "slack_enabled": False,
    "slack_webhook_url": "",
    # Deployment location, woven into the "coffee is ready" messages.
    "location": "B451",
}


class MachineConfig:
    """Read-only view of the machine config, with attribute-style access."""

    def __init__(self, values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]

    def get(self, key, default=None):
        return self._values.get(key, default)

    @property
    def serial_port(self):
        return self._values["serial_port"]

    @property
    def slack_enabled(self):
        return bool(self._values["slack_enabled"])

    @property
    def slack_webhook_url(self):
        return self._values["slack_webhook_url"]

    @property
    def location(self):
        return self._values["location"]

    def as_dict(self):
        return dict(self._values)

    def redacted(self):
        """as_dict() with the webhook masked -- safe to log/print."""
        d = dict(self._values)
        if d.get("slack_webhook_url"):
            d["slack_webhook_url"] = "<set>"
        return d


def _config_path(explicit=None):
    if explicit:
        return explicit
    return os.environ.get("BREWCOP_MACHINE_CONFIG", DEFAULT_PATH)


def load(path=None):
    """
    Load machine config, merging DEFAULTS <- TOML file <- env fallbacks.
    Never raises on a missing file (uses defaults); does raise on a file
    that exists but is malformed, so a provisioning typo is caught loudly.
    """
    values = dict(DEFAULTS)

    cfg_path = _config_path(path)
    try:
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        data = {}
    # tomllib.TOMLDecodeError intentionally propagates.

    # Only accept known keys, so a stray/misspelled key is ignored rather
    # than silently shadowing nothing useful.
    for key in DEFAULTS:
        if key in data:
            values[key] = data[key]

    # Environment fallback for the secret (dev convenience). Only fills in
    # when the file did not provide one.
    if not values["slack_webhook_url"]:
        env_url = os.environ.get("SLACK_WEBHOOK_URL")
        if env_url:
            values["slack_webhook_url"] = env_url

    return MachineConfig(values)


if __name__ == "__main__":
    # Quick manual check: print the resolved config (webhook redacted).
    import json

    cfg = load()
    print(json.dumps(cfg.redacted(), indent=2))

# vim: tabstop=4 shiftwidth=4 expandtab
