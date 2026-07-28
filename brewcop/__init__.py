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
brewcop: a Kivy touchscreen coffee monitor for the Technivorm at B451.

The package holds the app (app.py) and its headless-testable support modules
(brains, scale, currentsensor, potstate, brewsource, brewstate, brewnotify,
machineconfig, usersettings, backlight).  Nothing here imports Kivy at package
level, so the support modules stay cheap to import for unit tests -- only
app.py (and `python3 -m brewcop`) pull in the UI stack.
"""

__version__ = "0.1.0"

# vim: tabstop=4 shiftwidth=4 expandtab
