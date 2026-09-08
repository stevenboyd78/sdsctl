"""Test-only input/readback on the qualification harness's private Xvfb display."""

from __future__ import annotations

import ctypes as C
import os
import struct
import time
import zlib
from pathlib import Path


class Attributes(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "width", "height", "border", "depth")] + [
        ("visual", C.c_void_p),
        ("root", C.c_ulong),
        ("klass", C.c_int),
        ("bit_gravity", C.c_int),
        ("win_gravity", C.c_int),
        ("backing_store", C.c_int),
        ("backing_planes", C.c_ulong),
        ("backing_pixel", C.c_ulong),
        ("save_under", C.c_int),
        ("colormap", C.c_ulong),
        ("map_installed", C.c_int),
        ("map_state", C.c_int),
        ("all_events", C.c_long),
        ("your_events", C.c_long),
        ("do_not_propagate", C.c_long),
        ("override_redirect", C.c_int),
        ("screen", C.c_void_p),
    ]


class X11:
    def __init__(self):
        if not os.environ.get("DISPLAY") or not os.environ.get("XAUTHORITY"):
            raise RuntimeError("Private authenticated Xvfb display required")
        self.x = C.CDLL("libX11.so.6")
        self.t = C.CDLL("libXtst.so.6")
        self.error = False

        def failed(display, event):
            self.error = True
            return 0

        self.handler_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
        self.handler = self.handler_type(failed)
        self.x.XSetErrorHandler.argtypes = [self.handler_type]
        self.x.XSetErrorHandler.restype = C.c_void_p
        self.x.XSetErrorHandler(self.handler)  # Preserve Python cleanup on asynchronous X errors.
        for name, args, result in [
            ("XOpenDisplay", [C.c_char_p], C.c_void_p),
            ("XDefaultRootWindow", [C.c_void_p], C.c_ulong),
            (
                "XQueryTree",
                [
                    C.c_void_p,
                    C.c_ulong,
                    C.POINTER(C.c_ulong),
                    C.POINTER(C.c_ulong),
                    C.POINTER(C.POINTER(C.c_ulong)),
                    C.POINTER(C.c_uint),
                ],
                C.c_int,
            ),
            ("XFetchName", [C.c_void_p, C.c_ulong, C.POINTER(C.c_void_p)], C.c_int),
            ("XGetWindowAttributes", [C.c_void_p, C.c_ulong, C.POINTER(Attributes)], C.c_int),
            ("XInternAtom", [C.c_void_p, C.c_char_p, C.c_int], C.c_ulong),
            (
                "XGetWindowProperty",
                [
                    C.c_void_p,
                    C.c_ulong,
                    C.c_ulong,
                    C.c_long,
                    C.c_long,
                    C.c_int,
                    C.c_ulong,
                    C.POINTER(C.c_ulong),
                    C.POINTER(C.c_int),
                    C.POINTER(C.c_ulong),
                    C.POINTER(C.c_ulong),
                    C.POINTER(C.c_void_p),
                ],
                C.c_int,
            ),
            ("XFree", [C.c_void_p], C.c_int),
            ("XStringToKeysym", [C.c_char_p], C.c_ulong),
            ("XKeysymToKeycode", [C.c_void_p, C.c_ulong], C.c_uint),
            ("XSetInputFocus", [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong], C.c_int),
            ("XFlush", [C.c_void_p], C.c_int),
            ("XSync", [C.c_void_p, C.c_int], C.c_int),
            (
                "XGetImage",
                [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_ulong, C.c_int],
                C.c_void_p,
            ),
            ("XGetPixel", [C.c_void_p, C.c_int, C.c_int], C.c_ulong),
            ("XDestroyImage", [C.c_void_p], C.c_int),
            ("XCloseDisplay", [C.c_void_p], C.c_int),
            (
                "XCreateSimpleWindow",
                [
                    C.c_void_p,
                    C.c_ulong,
                    C.c_int,
                    C.c_int,
                    C.c_uint,
                    C.c_uint,
                    C.c_uint,
                    C.c_ulong,
                    C.c_ulong,
                ],
                C.c_ulong,
            ),
            ("XDestroyWindow", [C.c_void_p, C.c_ulong], C.c_int),
            ("XSetSelectionOwner", [C.c_void_p, C.c_ulong, C.c_ulong, C.c_ulong], C.c_int),
            ("XGetSelectionOwner", [C.c_void_p, C.c_ulong], C.c_ulong),
            (
                "XConvertSelection",
                [C.c_void_p, C.c_ulong, C.c_ulong, C.c_ulong, C.c_ulong, C.c_ulong],
                C.c_int,
            ),
        ]:
            function = getattr(self.x, name)
            function.argtypes, function.restype = args, result
        self.t.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.t.XTestFakeKeyEvent.restype = C.c_int
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Private display unavailable")
        self.root = self.x.XDefaultRootWindow(self.display)

    def windows(self):
        result = []

        def visit(window, depth):
            title = None
            atom = self.x.XInternAtom(self.display, b"_NET_WM_NAME", 0)
            actual, count, remaining = C.c_ulong(), C.c_ulong(), C.c_ulong()
            fmt, value = C.c_int(), C.c_void_p()
            if (
                self.x.XGetWindowProperty(
                    self.display,
                    window,
                    atom,
                    0,
                    1024,
                    0,
                    0,
                    C.byref(actual),
                    C.byref(fmt),
                    C.byref(count),
                    C.byref(remaining),
                    C.byref(value),
                )
                == 0
                and value
            ):
                if fmt.value == 8 and count.value:
                    title = C.string_at(value, count.value).decode("utf-8", "replace")
                self.x.XFree(value)
            name = C.c_void_p()
            if self.x.XFetchName(self.display, window, C.byref(name)) and name:
                title = title or C.string_at(name).decode("utf-8", "replace")
                self.x.XFree(name)
            attributes = Attributes()
            visible = self.x.XGetWindowAttributes(self.display, window, C.byref(attributes))
            if title and visible and attributes.map_state == 2:
                result.append((window, title))
            if depth == 0:
                return
            root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
            children = C.POINTER(C.c_ulong)()
            if self.x.XQueryTree(
                self.display,
                window,
                C.byref(root),
                C.byref(parent),
                C.byref(children),
                C.byref(count),
            ):
                values = list(children[: count.value])
                if children:
                    self.x.XFree(children)
                for child in values:
                    visit(child, depth - 1)

        visit(self.root, 3)
        return result

    def key(self, window, key):
        self.x.XSetInputFocus(self.display, window, 2, 0)
        code = self.x.XKeysymToKeycode(self.display, self.x.XStringToKeysym(key.encode()))
        if not code:
            raise RuntimeError("Fixture key unavailable")
        for pressed in (1, 0):
            if not self.t.XTestFakeKeyEvent(self.display, code, pressed, 0):
                raise RuntimeError("Fixture key not sent")
        self.x.XSync(self.display, 0)
        if self.error:
            raise RuntimeError("Private display input failed")

    def chord(self, window, modifier, key):
        self.x.XSetInputFocus(self.display, window, 2, 0)
        codes = [
            self.x.XKeysymToKeycode(self.display, self.x.XStringToKeysym(s.encode()))
            for s in (modifier, key)
        ]
        for code, pressed in ((codes[0], 1), (codes[1], 1), (codes[1], 0), (codes[0], 0)):
            if not code or not self.t.XTestFakeKeyEvent(self.display, code, pressed, 0):
                raise RuntimeError("Fixture chord not sent")
        self.x.XSync(self.display, 0)
        if self.error:
            raise RuntimeError("Private display input failed")

    def screenshot(self, path: Path):
        # Xvfb is deliberately fixed at 1280x1024x24. Read the test display,
        # never the physical display or the workstation desktop.
        width, height = 1280, 1024
        image = self.x.XGetImage(
            self.display, self.root, 0, 0, width, height, C.c_ulong(-1).value, 2
        )
        if not image:
            raise RuntimeError("Fixture screenshot unavailable")
        try:
            body = bytearray()
            for row in range(height):
                body.append(0)
                for column in range(width):
                    pixel = self.x.XGetPixel(image, column, row)
                    body.extend(((pixel >> 16) & 255, (pixel >> 8) & 255, pixel & 255))

            def chunk(kind, value):
                return (
                    struct.pack("!I", len(value))
                    + kind
                    + value
                    + struct.pack("!I", zlib.crc32(kind + value))
                )

            png = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(body))
                + chunk(b"IEND", b"")
            )
            with path.open("xb") as stream:
                stream.write(png)
        finally:
            self.x.XDestroyImage(image)

    def text(self, window):
        """Copy visible FICTIONAL fixture text on this private X server only.

        No DOM/debugger access or browser storage reads. Never call this helper
        on a real user's desktop, password form or production display.
        """
        clipboard = self.x.XInternAtom(self.display, b"CLIPBOARD", 0)
        utf8 = self.x.XInternAtom(self.display, b"UTF8_STRING", 0)
        prop = self.x.XInternAtom(self.display, b"SDSCTL_FIXTURE_TEXT", 0)
        receiver = self.x.XCreateSimpleWindow(self.display, self.root, 0, 0, 1, 1, 0, 0, 0)
        if not receiver:
            raise RuntimeError("Private selection receiver unavailable")
        try:
            self.x.XSetSelectionOwner(self.display, clipboard, 0, 0)
            self.chord(window, "Control_L", "a")
            self.chord(window, "Control_L", "c")
            deadline = time.monotonic() + 3
            while not self.x.XGetSelectionOwner(self.display, clipboard):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Private fixture copy unavailable")
                time.sleep(0.05)
            self.x.XConvertSelection(self.display, clipboard, utf8, prop, receiver, 0)
            self.x.XSync(self.display, 0)
            while time.monotonic() < deadline:
                actual, count, remaining = C.c_ulong(), C.c_ulong(), C.c_ulong()
                fmt, value = C.c_int(), C.c_void_p()
                status = self.x.XGetWindowProperty(
                    self.display,
                    receiver,
                    prop,
                    0,
                    16384,
                    0,
                    utf8,
                    C.byref(actual),
                    C.byref(fmt),
                    C.byref(count),
                    C.byref(remaining),
                    C.byref(value),
                )
                try:
                    if status == 0 and actual.value == utf8 and value:
                        if fmt.value != 8 or remaining.value or count.value > 65536:
                            raise RuntimeError("Private fixture text exceeded bounds")
                        return C.string_at(value, count.value).decode("utf-8", "strict")
                finally:
                    if value:
                        self.x.XFree(value)
                time.sleep(0.05)
            raise RuntimeError("Private fixture text deadline")
        finally:
            self.x.XDestroyWindow(self.display, receiver)

    def close(self):
        self.x.XCloseDisplay(self.display)
