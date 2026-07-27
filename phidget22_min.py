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
Minimal ctypes binding to libphidget22 -- just the VoltageInput slice.

The official `Phidget22` Python package is a large tree of generated ctypes
shims over the native libphidget22 C library.  brewcop only ever touches a
handful of those calls (open a VoltageInput on a VINT hub port, read a
voltage, close), so rather than vendor the whole generated package or take a
pip dependency, we hand-roll the sliver we use.  The C library itself is
apt-installable (libphidget22 / libphidget22-dev), which keeps brewcop's
"apt-only deps, no pip at install time" rule intact -- the only Python here
is this file.

Everything is one shape: pass an opaque handle plus a scalar, get back an
int PhidgetReturnCode (0 == EPHIDGET_OK).  There are no async callbacks in
our path because we attach synchronously via openWaitForAttachment(), which
is the one place a hand-rolled binding would otherwise get hairy.  On Linux
the calling convention is plain cdecl, so the header's __stdcall/CCONV
decoration (Windows-only) is irrelevant.

The class deliberately mirrors the upstream `VoltageInput` method names
(setHubPort, setIsHubPortDevice, setChannel, getVoltage, ...) so it is a
drop-in for `Phidget22.Devices.VoltageInput` -- callers (the bring-up probe,
and eventually currentsensor.py) do not care which one they got.

libphidget22 is loaded lazily on first VoltageInput() construction (the same
way scale.py lazily imports pyserial), so this module imports cleanly on a
dev box with neither the C library nor any hardware present.
"""

import ctypes
import ctypes.util

# PhidgetReturnCode: 0 is success (EPHIDGET_OK); anything else is an error.
EPHIDGET_OK = 0


class PhidgetError(Exception):
    """Base for all errors raised by this binding."""


class PhidgetBindingError(PhidgetError):
    """
    The binding itself could not be set up -- libphidget22 is not installed
    or not loadable.  Distinct from PhidgetException, which is a live error
    reported by the C library about a device/operation.
    """


class PhidgetException(PhidgetError):
    """A non-zero PhidgetReturnCode from a libphidget22 call."""

    def __init__(self, code, description=None):
        self.code = code
        if description is None:
            description = "error {}".format(code)
        super().__init__("Phidget error {}: {}".format(code, description))


# Loaded + prototyped once, on first use.  None until then.
_lib = None


def _load_library():
    """dlopen libphidget22, trying the usual sonames.  Raises on failure."""
    names = []
    found = ctypes.util.find_library("phidget22")
    if found:
        names.append(found)
    # find_library often misses versioned-only installs; try explicit sonames.
    names += ["libphidget22.so.0", "libphidget22.so"]

    errors = []
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError as e:
            errors.append("{}: {}".format(name, e))
    raise PhidgetBindingError(
        "could not load libphidget22 (install libphidget22 / "
        "libphidget22-dev). Tried: " + "; ".join(errors)
    )


def _configure_prototypes(lib):
    """
    Pin argtypes/restype for every call we use.  This is not optional: on
    64-bit, an un-prototyped pointer argument is treated as a C int and gets
    truncated, corrupting the handle.
    """
    handle = ctypes.c_void_p  # PhidgetHandle / PhidgetVoltageInputHandle
    rc = ctypes.c_uint32  # PhidgetReturnCode

    lib.PhidgetVoltageInput_create.argtypes = [ctypes.POINTER(handle)]
    lib.PhidgetVoltageInput_create.restype = rc
    lib.PhidgetVoltageInput_delete.argtypes = [ctypes.POINTER(handle)]
    lib.PhidgetVoltageInput_delete.restype = rc
    lib.PhidgetVoltageInput_getVoltage.argtypes = [handle, ctypes.POINTER(ctypes.c_double)]
    lib.PhidgetVoltageInput_getVoltage.restype = rc

    lib.Phidget_setDeviceSerialNumber.argtypes = [handle, ctypes.c_int32]
    lib.Phidget_setDeviceSerialNumber.restype = rc
    lib.Phidget_setHubPort.argtypes = [handle, ctypes.c_int]
    lib.Phidget_setHubPort.restype = rc
    lib.Phidget_setIsHubPortDevice.argtypes = [handle, ctypes.c_int]
    lib.Phidget_setIsHubPortDevice.restype = rc
    lib.Phidget_setChannel.argtypes = [handle, ctypes.c_int]
    lib.Phidget_setChannel.restype = rc
    lib.Phidget_openWaitForAttachment.argtypes = [handle, ctypes.c_uint32]
    lib.Phidget_openWaitForAttachment.restype = rc
    lib.Phidget_close.argtypes = [handle]
    lib.Phidget_close.restype = rc
    lib.Phidget_getErrorDescription.argtypes = [rc, ctypes.POINTER(ctypes.c_char_p)]
    lib.Phidget_getErrorDescription.restype = rc


def _get_lib():
    global _lib
    if _lib is None:
        lib = _load_library()
        _configure_prototypes(lib)
        _lib = lib
    return _lib


def _describe(lib, code):
    """Best-effort human string for a return code (never itself raises)."""
    s = ctypes.c_char_p()
    try:
        if lib.Phidget_getErrorDescription(code, ctypes.byref(s)) == EPHIDGET_OK and s.value:
            return s.value.decode("utf-8", "replace")
    except Exception:
        pass
    return None


def _check(lib, code):
    """Raise PhidgetException if a return code is not EPHIDGET_OK."""
    if code != EPHIDGET_OK:
        raise PhidgetException(code, _describe(lib, code))


class VoltageInput:
    """
    A single libphidget22 VoltageInput channel.

    Method names and semantics match the official Phidget22 Python
    VoltageInput so this can stand in for it unchanged.  Configure the
    address (serial / hub port / hub-port-device / channel) BEFORE calling
    openWaitForAttachment(), same as the real API.
    """

    def __init__(self):
        self._lib = _get_lib()
        self._handle = ctypes.c_void_p()
        _check(self._lib, self._lib.PhidgetVoltageInput_create(ctypes.byref(self._handle)))

    def setDeviceSerialNumber(self, serial):
        _check(self._lib, self._lib.Phidget_setDeviceSerialNumber(self._handle, serial))

    def setHubPort(self, port):
        _check(self._lib, self._lib.Phidget_setHubPort(self._handle, port))

    def setIsHubPortDevice(self, is_hub_port_device):
        _check(
            self._lib,
            self._lib.Phidget_setIsHubPortDevice(self._handle, 1 if is_hub_port_device else 0),
        )

    def setChannel(self, channel):
        _check(self._lib, self._lib.Phidget_setChannel(self._handle, channel))

    def openWaitForAttachment(self, timeout_ms):
        _check(self._lib, self._lib.Phidget_openWaitForAttachment(self._handle, timeout_ms))

    def getVoltage(self):
        value = ctypes.c_double()
        _check(self._lib, self._lib.PhidgetVoltageInput_getVoltage(self._handle, ctypes.byref(value)))
        return value.value

    def close(self):
        if self._handle:
            self._lib.Phidget_close(self._handle)  # ignore rc: closing is best-effort

    def __del__(self):
        # Free both the channel and the underlying handle.  Guard everything:
        # __del__ must never raise, and may run during interpreter teardown
        # when module globals are already gone.
        try:
            if getattr(self, "_handle", None):
                self.close()
                self._lib.PhidgetVoltageInput_delete(ctypes.byref(self._handle))
                self._handle = ctypes.c_void_p()
        except Exception:
            pass


# vim: tabstop=4 shiftwidth=4 expandtab
