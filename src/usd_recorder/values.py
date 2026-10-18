"""Typed values: which USD type a Python value is recorded as.

Pure Python except for :data:`SDF_TYPES`, which maps each type name to its ``Sdf`` value type
and a converter. See ARCHITECTURE.md § Typed values.

========== ============================== =====================
Type name  Python value                   USD type
========== ============================== =====================
``bool``   ``bool``                       ``bool``
``int``    ``int`` in the signed 32-bit   ``int``
           range
``double`` ``float``                      ``double``
``string`` ``str``                        ``string``
``double3`` a tuple or list of three      ``double3``
           numbers (not bools)
========== ============================== =====================

A key keeps the type of its first value for the whole recording; a later value of another type
is an error (:class:`TypeChangeError`), never a silent re-typing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Any

from pxr import Gf, Sdf, Vt

INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1
_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(:[A-Za-z_][A-Za-z0-9_]*)*$")

Vec3 = tuple[float, float, float]


class TypeChangeError(ValueError):
    """A key was given a value of a different type than its first value."""


def is_valid_property_name(name: str) -> bool:
    """Whether ``name`` (possibly namespaced with ``:``) is a valid USD property name."""
    return isinstance(name, str) and bool(_PROPERTY_NAME.fullmatch(name))


def _is_number_triple(value: Any) -> bool:
    return (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in value)
    )


def as_vec3(value: Sequence[float]) -> Vec3:
    """Three numbers as a tuple of floats; ``ValueError`` if a component overflows a double."""
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except OverflowError:
        raise ValueError(f"vector {value!r} has a component too large for a double") from None


def value_type(value: Any) -> str:
    """The type name a value is recorded as: ``bool``, ``int``, ``double``, ``string`` or
    ``double3``.

    Raises ``TypeError`` for any other value and ``ValueError`` for one the USD type cannot
    hold (an ``int`` outside the signed 32-bit range, or a vector component too large for a
    double).
    """
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        if not INT32_MIN <= value <= INT32_MAX:
            raise ValueError(f"int {value} is outside the 32-bit range of a USD int")
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    if _is_number_triple(value):
        as_vec3(value)
        return "double3"
    raise TypeError(f"unsupported value {value!r} ({type(value).__name__})")


def normalize(value: Any) -> Any:
    """The comparable Python form of a value: vectors become tuples of floats."""
    if value_type(value) == "double3":
        return as_vec3(value)
    return value


def normalize_color(value: Any) -> Vec3:
    """Validate a colour: three numbers in ``[0, 1]``."""
    if not _is_number_triple(value):
        raise ValueError(f"a colour must be three numbers in [0, 1], got {value!r}")
    color = as_vec3(value)
    if not all(0.0 <= c <= 1.0 for c in color):
        raise ValueError(f"a colour must be three numbers in [0, 1], got {value!r}")
    return color


def is_finite_vec3(value: Any) -> bool:
    return _is_number_triple(value) and all(math.isfinite(float(c)) for c in value)


def to_vec3d(value: Sequence[float]) -> Gf.Vec3d:
    return Gf.Vec3d(*value)


def to_color_array(value: Sequence[float]) -> Vt.Vec3fArray:
    """A colour as the one-element ``primvars:displayColor`` array."""
    return Vt.Vec3fArray([Gf.Vec3f(*value)])


SDF_TYPES: dict[str, tuple[Sdf.ValueTypeName, Callable[[Any], Any]]] = {
    "bool": (Sdf.ValueTypeNames.Bool, bool),
    "int": (Sdf.ValueTypeNames.Int, int),
    "double": (Sdf.ValueTypeNames.Double, float),
    "string": (Sdf.ValueTypeNames.String, str),
    "double3": (Sdf.ValueTypeNames.Double3, to_vec3d),
}


__all__ = [
    "INT32_MAX",
    "INT32_MIN",
    "SDF_TYPES",
    "TypeChangeError",
    "as_vec3",
    "is_finite_vec3",
    "is_valid_property_name",
    "normalize",
    "normalize_color",
    "to_color_array",
    "to_vec3d",
    "value_type",
]
