"""Sparse time sampling with hold keys for one attribute.

USD interpolates numeric samples linearly (by default) and holds the first sample backwards and
the last sample forwards. Writing a sample only when a value *changes* is therefore not enough:
an unchanged run between two changes would interpolate. :class:`Track` writes

1. the first observed value;
2. nothing for an unchanged value, but remembers its time as the *pending hold*;
3. on a change at time ``t``: the pending hold (the old value at the last time it was still
   current), then the new value at ``t``;
4. on :meth:`flush`: any pending hold, so the final value holds through the end of the range.

The result resolves to exactly the observed value at every observed time code, with at most two
samples per change. See ARCHITECTURE.md § Sparse sampling.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pxr import Usd


def _identity(value: Any) -> Any:
    return value


class Track:
    """Sparse sampler for one ``Usd.Attribute`` (any type ``convert`` produces a value for)."""

    __slots__ = ("attr", "value", "has_value", "pending", "convert", "samples")

    def __init__(self, attr: Usd.Attribute, convert: Callable[[Any], Any] = _identity):
        self.attr = attr
        self.value: Any = None
        self.has_value = False
        self.pending: float | None = None
        self.convert = convert
        self.samples = 0

    def _write(self, time: float, value: Any) -> None:
        self.attr.Set(self.convert(value), Usd.TimeCode(time))
        self.samples += 1

    def observe(self, time: float, value: Any) -> None:
        """``value`` is current at ``time`` (times must not decrease)."""
        if not self.has_value:
            self._write(time, value)
            self.value, self.has_value, self.pending = value, True, None
        elif value == self.value:
            self.pending = time
        else:
            if self.pending is not None:
                self._write(self.pending, self.value)
            self._write(time, value)
            self.value, self.pending = value, None

    def hold(self, time: float) -> None:
        """The value is unchanged at ``time`` without being observed (e.g. while hidden)."""
        if self.has_value:
            self.pending = time

    def flush(self) -> None:
        """Write the pending hold, if any."""
        if self.pending is not None:
            self._write(self.pending, self.value)
            self.pending = None


__all__ = ["Track"]
