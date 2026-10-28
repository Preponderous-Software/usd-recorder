"""usd-recorder: record a simulation tick by tick as a time-sampled OpenUSD stage."""

__version__ = "0.3.0"

from .atomic import atomic_export, atomic_write_bytes, atomic_write_text  # noqa: E402
from .clips import (  # noqa: E402
    chunk_name,
    default_flat_path,
    default_invisible,
    flatten,
    has_clips,
    set_manifest_defaults,
    stitch,
)
from .inspect import inspect_stage  # noqa: E402
from .recorder import Recorder, default_entity_name  # noqa: E402
from .track import Track  # noqa: E402
from .values import SDF_TYPES, TypeChangeError, normalize_color, value_type  # noqa: E402

__all__ = [
    "Recorder",
    "SDF_TYPES",
    "Track",
    "TypeChangeError",
    "__version__",
    "atomic_export",
    "atomic_write_bytes",
    "atomic_write_text",
    "chunk_name",
    "default_entity_name",
    "default_flat_path",
    "default_invisible",
    "flatten",
    "has_clips",
    "inspect_stage",
    "normalize_color",
    "set_manifest_defaults",
    "stitch",
    "value_type",
]
