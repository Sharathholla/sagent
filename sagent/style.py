"""Terminal colours. Disabled when output is not a TTY or NO_COLOR is set; FORCE_COLOR enables. Stdlib only."""

from __future__ import annotations

import os
import sys
from typing import Dict, Optional, TextIO

_CODES = {
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",
    "bold": "1", "dim": "2",
}
_cache: Dict[int, bool] = {}


def _enable_windows_ansi() -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ok = True
        for handle_id in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ok = False
                continue
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            ok = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004)) and ok
        return ok
    except Exception:  # noqa: BLE001
        return False


def enabled(stream: Optional[TextIO] = None) -> bool:
    stream = stream or sys.stdout
    key = id(stream)
    if key not in _cache:
        if os.environ.get("NO_COLOR"):
            result = False
        elif os.environ.get("FORCE_COLOR"):
            result = True
        elif os.environ.get("TERM") == "dumb" or not hasattr(stream, "isatty") or not stream.isatty():
            result = False
        else:
            result = _enable_windows_ansi() if os.name == "nt" else True
        _cache[key] = result
    return _cache[key]


def paint(text: str, *styles: str, stream: Optional[TextIO] = None) -> str:
    if not styles or not enabled(stream):
        return text
    codes = ";".join(_CODES[s] for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def red(text: str, stream: Optional[TextIO] = None) -> str:
    return paint(text, "red", stream=stream)


def green(text: str, stream: Optional[TextIO] = None) -> str:
    return paint(text, "green", stream=stream)


def yellow(text: str, stream: Optional[TextIO] = None) -> str:
    return paint(text, "yellow", stream=stream)


def cyan(text: str, stream: Optional[TextIO] = None) -> str:
    return paint(text, "cyan", stream=stream)


def dim(text: str, stream: Optional[TextIO] = None) -> str:
    return paint(text, "dim", stream=stream)


def bold(text: str, stream: Optional[TextIO] = None) -> str:
    return paint(text, "bold", stream=stream)


def heading(text: str) -> str:
    return paint(text, "bold", "cyan")


def error(message: str) -> None:
    print(paint("error:", "bold", "red", stream=sys.stderr) + " " + red(message, sys.stderr), file=sys.stderr)


def warning(message: str) -> None:
    print(paint("warning:", "bold", "yellow", stream=sys.stderr) + " " + yellow(message, sys.stderr), file=sys.stderr)


def note(message: str) -> None:
    print(paint("note:", "bold", "cyan", stream=sys.stderr) + " " + message, file=sys.stderr)


def success(message: str) -> None:
    print(green(message))


STATUS_STYLE = {"ok": ("bold", "green"), "FAIL": ("bold", "red"), "warn": ("bold", "yellow"),
                "skip": ("dim",), "info": ("bold", "cyan")}


def status(level: str, text: str) -> str:
    tag = paint(f"[{level:^4}]", *STATUS_STYLE.get(level, ()))
    if level == "FAIL":
        text = red(text)
    elif level == "warn":
        text = yellow(text)
    elif level == "skip":
        text = dim(text)
    return f"{tag} {text}"
