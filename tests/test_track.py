from pxr import Sdf, Usd

from usd_recorder import Track

_STAGES = []  # keep each in-memory stage alive while its attribute is used


def make_attr():
    stage = Usd.Stage.CreateInMemory()
    _STAGES.append(stage)
    prim = stage.DefinePrim("/P")
    return prim.CreateAttribute("x", Sdf.ValueTypeNames.Double)


def test_unchanged_values_write_no_samples_until_a_change():
    attr = make_attr()
    track = Track(attr)
    for t, v in enumerate([1.0, 1.0, 1.0, 1.0, 5.0, 5.0]):
        track.observe(t, v)
    assert attr.GetTimeSamples() == [0.0, 3.0, 4.0]
    track.flush()
    assert attr.GetTimeSamples() == [0.0, 3.0, 4.0, 5.0]


def test_hold_keys_stop_interpolation():
    attr = make_attr()
    track = Track(attr)
    for t, v in [(0, 0.0), (1, 0.0), (2, 0.0), (10, 10.0)]:
        track.observe(t, v)
    track.flush()
    assert attr.Get(2) == 0.0  # without the hold key at 2 this would be 2.0
    assert attr.Get(6) == 5.0  # between observed ticks USD still interpolates (6 was not observed)


def test_hold_without_observing_and_flush_is_idempotent():
    attr = make_attr()
    track = Track(attr, convert=float)
    track.hold(0)  # nothing observed yet: no effect
    track.observe(1, 3)
    track.hold(4)
    track.flush()
    track.flush()
    assert attr.GetTimeSamples() == [1.0, 4.0]
    assert track.samples == 2


def test_every_observed_time_resolves_to_its_value():
    import random

    rng = random.Random(5)
    attr = make_attr()
    track = Track(attr)
    seen = []
    for t in range(200):
        v = float(rng.choice([0, 0, 0, 1, 2]))
        track.observe(t, v)
        seen.append(v)
    track.flush()
    assert [attr.Get(t) for t in range(200)] == seen
