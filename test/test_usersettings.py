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

"""Tests for usersettings: writable JSON settings store."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import usersettings  # noqa: E402


class TestUserSettings(unittest.TestCase):
    def test_defaults_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            s = usersettings.UserSettings(os.path.join(d, "none.json"))
            self.assertEqual(s["pot_tare_g"], 795)
            self.assertEqual(s["slack_enabled"], False)
            self.assertAlmostEqual(s["dim_level"], 0.15)
            self.assertEqual(s["brew_target_ml"], 1250)

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "config.json")  # dir created on save
            s = usersettings.UserSettings(p)
            s["stale_hours"] = 6.5
            s["slack_enabled"] = True
            s["pot_capacity_ml"] = 1000
            s.save()
            s2 = usersettings.UserSettings(p)
            self.assertEqual(s2["stale_hours"], 6.5)
            self.assertEqual(s2["slack_enabled"], True)
            self.assertEqual(s2["pot_capacity_ml"], 1000)
            # untouched key keeps its default
            self.assertEqual(s2["pot_tare_g"], 795)

    def test_corrupt_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w") as f:
                f.write("{ this is not valid json")
            s = usersettings.UserSettings(p)  # must not raise
            self.assertEqual(s["pot_tare_g"], 795)

    def test_unknown_keys_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w") as f:
                f.write('{"pot_tare_g": 800, "bogus_key": 1}')
            s = usersettings.UserSettings(p)
            self.assertEqual(s["pot_tare_g"], 800)
            self.assertNotIn("bogus_key", s.values)

    def test_env_var_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "env.json")
            old = os.environ.get("BREWCOP_CONFIG")
            os.environ["BREWCOP_CONFIG"] = p
            try:
                s = usersettings.UserSettings()  # no explicit path
                s["dim_timeout_s"] = 300
                s.save()
                self.assertEqual(usersettings.UserSettings()["dim_timeout_s"], 300)
            finally:
                if old is None:
                    os.environ.pop("BREWCOP_CONFIG")
                else:
                    os.environ["BREWCOP_CONFIG"] = old


if __name__ == "__main__":
    unittest.main()

# vim: tabstop=4 shiftwidth=4 expandtab
