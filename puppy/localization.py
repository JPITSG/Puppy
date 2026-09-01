"""Small, runtime-only locale facts exposed to the WebUI.

The browser and the Puppy process can have different locales.  Clock labels in
the console follow the process serving that console, so derive the hour cycle
from LC_TIME instead of letting every browser choose independently.
"""
from __future__ import annotations

from functools import lru_cache
import locale
import time


def initialize() -> None:
    """Adopt the host environment's LC_TIME once, before worker threads run."""
    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        # A named locale can be configured but absent from a small container.
        # Retaining the process default is safer than preventing Puppy startup.
        pass
    clock_format.cache_clear()


def _time_directives(pattern: str) -> set:
    """Return strftime directive letters, ignoring literals and modifiers."""
    directives = set()
    index = 0
    while index < len(pattern):
        if pattern[index] != "%":
            index += 1
            continue
        index += 1
        if index >= len(pattern):
            break
        if pattern[index] == "%":
            index += 1
            continue
        if pattern[index] in ("E", "O"):
            index += 1
            if index >= len(pattern):
                break
        directives.add(pattern[index])
        index += 1
    return directives


def clock_format_from_pattern(pattern: str):
    """Map a locale T_FMT pattern to ``12h``/``24h`` when it is decisive."""
    directives = _time_directives(str(pattern or ""))
    uses_12 = bool(directives.intersection({"I", "l", "r"}))
    uses_24 = bool(directives.intersection({"H", "k", "R", "T"}))
    if uses_12 and not uses_24:
        return "12h"
    if uses_24 and not uses_12:
        return "24h"
    # Some platforms spell the 12-hour pattern out without %I/%r but retain a
    # day-period marker.  It is useful only when no hour directive contradicted
    # it; a redundant "%H ... %p" remains a 24-hour clock.
    if not uses_24 and directives.intersection({"p", "P"}):
        return "12h"
    return None


@lru_cache(maxsize=1)
def clock_format() -> str:
    """The server process's default hour cycle, as ``12h`` or ``24h``."""
    try:
        detected = clock_format_from_pattern(locale.nl_langinfo(locale.T_FMT))
    except (AttributeError, ValueError, locale.Error):
        detected = None
    if detected:
        return detected

    # Portable fallback for an opaque %X/%c-style pattern.  A default 12-hour
    # rendering normally includes the locale's own non-empty AM/PM marker.
    sample = (2000, 1, 1, 13, 5, 0, 5, 1, -1)
    try:
        rendered = time.strftime("%X", sample).casefold()
        marker = time.strftime("%p", sample).strip().casefold()
        if marker and marker in rendered:
            return "12h"
    except (OverflowError, ValueError):
        pass
    # Unknown locales fail closed to an unambiguous 00..23 clock.
    return "24h"
