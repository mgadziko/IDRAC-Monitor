"""Small X11 helper for persisting a GTK4 window's screen position."""

from __future__ import annotations

import ctypes
from typing import Any


class X11WindowGeometry:
    """Read/move a realized GDK X11 surface without extra Python packages."""

    def __init__(self) -> None:
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x11.XDisplayWidth.restype = ctypes.c_int
        self.x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x11.XDisplayHeight.restype = ctypes.c_int
        self.x11.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        self.x11.XTranslateCoordinates.restype = ctypes.c_int
        self.x11.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        self.x11.XMoveWindow.restype = ctypes.c_int
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XSetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
        self.x11.XSetClassHint.restype = ctypes.c_int

    @staticmethod
    def _xid(window: Any) -> int:
        from gi.repository import GdkX11

        surface = window.get_surface()
        if surface is None:
            raise RuntimeError("The window has not been realized yet.")
        return int(GdkX11.X11Surface.get_xid(surface))

    def position(self, window: Any) -> tuple[int, int] | None:
        display = self.x11.XOpenDisplay(None)
        if not display:
            return None
        try:
            root = self.x11.XDefaultRootWindow(display)
            x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
            ok = self.x11.XTranslateCoordinates(
                display, self._xid(window), root, 0, 0,
                ctypes.byref(x), ctypes.byref(y), ctypes.byref(child),
            )
            return (x.value, y.value) if ok else None
        except (ImportError, TypeError, RuntimeError):
            return None
        finally:
            self.x11.XCloseDisplay(display)

    def move(self, window: Any, x: int, y: int) -> bool:
        display = self.x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            xid = self._xid(window)
            width = self.x11.XDisplayWidth(display, 0)
            height = self.x11.XDisplayHeight(display, 0)
            window_width = max(1, window.get_width())
            window_height = max(1, window.get_height())
            x = max(0, min(x, max(0, width - window_width)))
            y = max(0, min(y, max(0, height - window_height)))
            self.x11.XMoveWindow(display, xid, x, y)
            self.x11.XFlush(display)
            return True
        except (ImportError, TypeError, RuntimeError):
            return False
        finally:
            self.x11.XCloseDisplay(display)

    def set_class_hint(self, window: Any, name: str, resource_class: str) -> bool:
        class XClassHint(ctypes.Structure):
            _fields_ = [("res_name", ctypes.c_char_p), ("res_class", ctypes.c_char_p)]

        display = self.x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            hint = XClassHint(name.encode(), resource_class.encode())
            self.x11.XSetClassHint(display, self._xid(window), ctypes.byref(hint))
            self.x11.XFlush(display)
            return True
        except (ImportError, TypeError, RuntimeError):
            return False
        finally:
            self.x11.XCloseDisplay(display)
