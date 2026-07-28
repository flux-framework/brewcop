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

"""Entry point for `python3 -m brewcop`."""

from .app import main

if __name__ == "__main__":
    main()

# vim: tabstop=4 shiftwidth=4 expandtab
