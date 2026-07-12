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

"""Tests for brewstate: brew-state persistence across reboots."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import brewstate  # noqa: E402


class TestBrewState(unittest.TestCase):
    def test_load_absent_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(brewstate.load(os.path.join(d, "none.json")))

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "brewstate.json")  # dir made on save
            snap = {"state": "ready", "ready_time": 1234.5, "timestamp": 1234.5}
            self.assertTrue(brewstate.save(snap, p))
            self.assertEqual(brewstate.load(p), snap)

    def test_corrupt_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "brewstate.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertIsNone(brewstate.load(p))

    def test_state_path_prefers_env(self):
        old = os.environ.get("BREWCOP_STATE")
        os.environ["BREWCOP_STATE"] = "/tmp/explicit-brewstate.json"
        try:
            self.assertEqual(brewstate.state_path(), "/tmp/explicit-brewstate.json")
        finally:
            if old is None:
                os.environ.pop("BREWCOP_STATE")
            else:
                os.environ["BREWCOP_STATE"] = old

    def test_state_path_beside_config(self):
        # with no BREWCOP_STATE but a BREWCOP_CONFIG, sit beside settings
        saved = {k: os.environ.get(k) for k in ("BREWCOP_STATE", "BREWCOP_CONFIG")}
        os.environ.pop("BREWCOP_STATE", None)
        os.environ["BREWCOP_CONFIG"] = "/var/lib/brewcop/config.json"
        try:
            self.assertEqual(brewstate.state_path(), "/var/lib/brewcop/brewstate.json")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
