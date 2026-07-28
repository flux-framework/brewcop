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
Publish brew domain events to MQTT.

brewcop is notification-agnostic: it emits a `ready` event to the broker and a
separate, redeployable consumer decides policy (Slack, signage, telemetry).
This keeps the on-device app free of any one channel's secrets and outbound
HTTP -- the broker/consumer live off the Pi.

Fire-and-forget: publish_ready() swallows every failure (broker down, paho
missing, misconfig) and never raises, so a publish can't wedge the poll tick.
An empty mqtt_host means MQTT is unconfigured -> silent no-op.

Uses paho's one-shot publish.single(): connect/publish/disconnect per event,
no long-lived client to own or reconnect.  Imported lazily so a dev box
without python3-paho-mqtt still runs the app.
"""

import json
import sys


def publish_ready(config, result):
    """Publish a brewing->ready event to MQTT.  Returns True if the publish
    was attempted and succeeded, False otherwise (unconfigured, paho absent,
    or broker error).  Never raises."""
    host = getattr(config, "mqtt_host", "") or ""
    if not host:
        return False  # MQTT unconfigured -> no-op

    try:
        import paho.mqtt.publish as publish

        location = config.location
        topic = "{}/{}/ready".format(config.mqtt_topic_prefix, location)
        ml = (result.raw_grams or 0.0) if result is not None else 0.0
        amps = result.amps if result is not None else None
        payload = json.dumps(
            {
                "event": "ready",
                "location": location,
                "ml": round(ml),
                "amps": amps,
            }
        )
        publish.single(
            topic,
            payload=payload,
            hostname=host,
            port=config.mqtt_port,
            retain=True,
        )
        return True
    except Exception as e:
        print("mqtt publish failed: {}".format(e), file=sys.stderr)
        return False


# vim: tabstop=4 shiftwidth=4 expandtab
