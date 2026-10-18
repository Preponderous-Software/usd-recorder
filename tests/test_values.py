import pytest
from pxr import Gf

from usd_recorder import normalize_color, value_type
from usd_recorder.values import SDF_TYPES, as_vec3, is_valid_property_name, normalize


@pytest.mark.parametrize(
    "value, name",
    [(True, "bool"), (3, "int"), (2.5, "double"), ("x", "string"), ((1, 2, 3), "double3"), ([0.0, 1, 2.5], "double3")],
)
def test_value_types(value, name):
    assert value_type(value) == name
    sdf_type, convert = SDF_TYPES[name]
    convert(value)  # every type converts


@pytest.mark.parametrize("value", [None, b"x", {}, (1, 2), (1, 2, 3, 4), (True, 1, 2), ("a", 1, 2), 1j])
def test_unsupported_values(value):
    with pytest.raises(TypeError):
        value_type(value)


def test_int_range_and_huge_vectors():
    assert value_type(2**31 - 1) == "int"
    with pytest.raises(ValueError):
        value_type(2**31)
    with pytest.raises(ValueError):
        value_type((10**400, 0, 0))
    with pytest.raises(ValueError):
        as_vec3((10**400, 0, 0))


def test_colors():
    assert normalize_color([0, 1, 0.5]) == (0.0, 1.0, 0.5)
    for bad in [(1.1, 0, 0), (-0.1, 0, 0), (0, 0), "red", (True, 0, 0)]:
        with pytest.raises(ValueError):
            normalize_color(bad)


def test_property_names_and_normalize():
    assert is_valid_property_name("a:b_c")
    for bad in ["", "1a", "a b", "a::b", ":a", "a-b", None]:
        assert not is_valid_property_name(bad)
    assert normalize([1, 2, 3]) == (1.0, 2.0, 3.0)
    assert SDF_TYPES["double3"][1]((1, 2, 3)) == Gf.Vec3d(1, 2, 3)
