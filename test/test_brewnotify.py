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

"""Tests for brewnotify: the fire-and-forget MQTT publish seam.

paho need not be installed: a fake ``paho.mqtt.publish`` module is injected
into sys.modules so the lazy import inside publish_ready() resolves to it.
"""

import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from brewcop import brewnotify  # noqa: E402


class FakeConfig:
    """Duck-typed stand-in for MachineConfig."""

    def __init__(self, host="", port=1883, prefix="brewcop", location="B451"):
        self.mqtt_host = host
        self.mqtt_port = port
        self.mqtt_topic_prefix = prefix
        self.location = location


class FakeResult:
    def __init__(self, raw_grams=900.0, amps=0.5):
        self.raw_grams = raw_grams
        self.amps = amps


def install_fake_paho(single):
    """Put a fake paho.mqtt.publish (with the given single()) on sys.modules;
    return a callable that restores the prior state."""
    saved = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.publish")}
    paho = types.ModuleType("paho")
    mqtt = types.ModuleType("paho.mqtt")
    publish = types.ModuleType("paho.mqtt.publish")
    publish.single = single
    mqtt.publish = publish
    paho.mqtt = mqtt
    sys.modules["paho"] = paho
    sys.modules["paho.mqtt"] = mqtt
    sys.modules["paho.mqtt.publish"] = publish

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    return restore


class TestPublishReady(unittest.TestCase):
    def test_no_host_is_noop(self):
        # Unconfigured broker -> no publish attempted, returns False.
        calls = []
        restore = install_fake_paho(lambda *a, **k: calls.append((a, k)))
        try:
            sent = brewnotify.publish_ready(FakeConfig(host=""), FakeResult())
        finally:
            restore()
        self.assertFalse(sent)
        self.assertEqual(calls, [])

    def test_publish_swallows_errors(self):
        # A broker/paho error must be swallowed: False, never raised.
        def boom(*a, **k):
            raise OSError("connection refused")

        restore = install_fake_paho(boom)
        try:
            sent = brewnotify.publish_ready(
                FakeConfig(host="localhost"), FakeResult()
            )
        finally:
            restore()
        self.assertFalse(sent)

    def test_builds_topic_and_payload(self):
        captured = {}

        def single(topic, **kwargs):
            captured["topic"] = topic
            captured["kwargs"] = kwargs

        restore = install_fake_paho(single)
        try:
            sent = brewnotify.publish_ready(
                FakeConfig(host="broker", port=1884, location="B451"),
                FakeResult(raw_grams=912.4, amps=0.5),
            )
        finally:
            restore()
        self.assertTrue(sent)
        self.assertEqual(captured["topic"], "brewcop/B451/ready")
        self.assertEqual(captured["kwargs"]["hostname"], "broker")
        self.assertEqual(captured["kwargs"]["port"], 1884)
        self.assertTrue(captured["kwargs"]["retain"])
        payload = json.loads(captured["kwargs"]["payload"])
        self.assertEqual(payload["event"], "ready")
        self.assertEqual(payload["location"], "B451")
        self.assertEqual(payload["ml"], 912)  # rounded grams ~ mL

    def test_missing_paho_is_swallowed(self):
        # A None entry in sys.modules forces the lazy `import paho.mqtt.publish`
        # to raise ImportError -- deterministic whether or not paho is really
        # installed.  The seam must swallow it and return False.
        keys = ("paho", "paho.mqtt", "paho.mqtt.publish")
        saved = {k: sys.modules.get(k) for k in keys}
        for k in keys:
            sys.modules[k] = None
        try:
            sent = brewnotify.publish_ready(
                FakeConfig(host="broker"), FakeResult()
            )
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
