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
provisioned and never written at runtime: the serial port path, the MQTT
broker host/port/topic-prefix that `ready` events publish to, and the
location string.  There is no secret here (notification policy and any
webhook secrets live in the downstream MQTT consumer, off the Pi).  Because
it is never written while running, it lives fine on the read-only root
(unlike the user-tweakable settings, which need a writable partition).

Distinct from the user settings (pot tare/capacity, etc.) edited via the
touchscreen Settings screen -- see MODERNIZATION.md, "Two configs".

Load precedence (later overrides earlier):
  1. built-in DEFAULTS
  2. TOML file (explicit path, else $BREWCOP_MACHINE_CONFIG, else
     /etc/brewcop/config.toml)  -- absent file is fine

Read via stdlib tomllib (Python 3.11+), so no extra apt dependency.
"""

import os
import tomllib


DEFAULT_PATH = "/etc/brewcop/config.toml"

DEFAULTS = {
    # Serial device for the scale. "auto" -> autodetect a USB adapter.
    "serial_port": "auto",
    # MQTT broker that `ready` events publish to.  Empty host = MQTT off
    # (the app runs fine, it just doesn't publish).  What to DO with a
    # published event is the downstream consumer's job, not brewcop's.
    "mqtt_host": "",
    "mqtt_port": 1883,
    "mqtt_topic_prefix": "brewcop",
    # Deployment location, woven into the topic and event payload.
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
    def mqtt_host(self):
        return self._values["mqtt_host"]

    @property
    def mqtt_port(self):
        return self._values["mqtt_port"]

    @property
    def mqtt_topic_prefix(self):
        return self._values["mqtt_topic_prefix"]

    @property
    def location(self):
        return self._values["location"]

    def as_dict(self):
        return dict(self._values)


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

    return MachineConfig(values)


if __name__ == "__main__":
    # Quick manual check: print the resolved config.
    import json

    cfg = load()
    print(json.dumps(cfg.as_dict(), indent=2))

# vim: tabstop=4 shiftwidth=4 expandtab
