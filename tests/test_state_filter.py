"""TDD tests for tools.state_filter.

Covers each operator (==, <, <=, >, >=), multi-key AND, missing-state
non-match, and the two state-source paths (.states dict vs direct attr).
Uses a tiny Device class instead of MagicMock so attribute presence is
real (MagicMock auto-vivifies anything you access).
"""


class _Device:
    """Bare-bones device stand-in with optional .states dict.

    Real Indigo devices put core attrs (onState, brightness, etc.) on
    the object directly and plugin-managed state in a .states dict.
    Tests need both paths; using a real class makes "attribute exists?"
    deterministic, unlike MagicMock.
    """

    def __init__(self, states=None, **attrs):
        if states is not None:
            self.states = states
        for k, v in attrs.items():
            setattr(self, k, v)


# ----- equality (default for non-string values) -------------------------


def test_eq_with_bool_direct_attr():
    from tools.state_filter import matches

    on_dev = _Device(onState=True)
    off_dev = _Device(onState=False)
    assert matches(on_dev, {"onState": True}) is True
    assert matches(off_dev, {"onState": True}) is False


def test_eq_with_int_direct_attr():
    from tools.state_filter import matches

    dev = _Device(brightness=50)
    assert matches(dev, {"brightness": 50}) is True
    assert matches(dev, {"brightness": 75}) is False


# ----- comparators on numeric state ------------------------------------


def test_lt_string_prefix():
    from tools.state_filter import matches

    low = _Device(states={"batteryLevel": 15})
    high = _Device(states={"batteryLevel": 80})
    assert matches(low, {"batteryLevel": "<20"}) is True
    assert matches(high, {"batteryLevel": "<20"}) is False


def test_lte_string_prefix():
    from tools.state_filter import matches

    boundary = _Device(states={"batteryLevel": 20})
    over = _Device(states={"batteryLevel": 21})
    assert matches(boundary, {"batteryLevel": "<=20"}) is True
    assert matches(over, {"batteryLevel": "<=20"}) is False


def test_gt_string_prefix():
    from tools.state_filter import matches

    high = _Device(brightness=75)
    low = _Device(brightness=25)
    assert matches(high, {"brightness": ">50"}) is True
    assert matches(low, {"brightness": ">50"}) is False


def test_gte_string_prefix():
    from tools.state_filter import matches

    boundary = _Device(brightness=50)
    under = _Device(brightness=49)
    assert matches(boundary, {"brightness": ">=50"}) is True
    assert matches(under, {"brightness": ">=50"}) is False


# ----- multi-key AND ----------------------------------------------------


def test_multi_key_and_all_match():
    from tools.state_filter import matches

    dev = _Device(onState=True, brightness=80)
    assert matches(dev, {"onState": True, "brightness": ">=50"}) is True


def test_multi_key_and_one_fails():
    from tools.state_filter import matches

    dev = _Device(onState=True, brightness=20)
    # onState matches but brightness < 50, so the whole spec is False.
    assert matches(dev, {"onState": True, "brightness": ">=50"}) is False


# ----- missing state ----------------------------------------------------


def test_missing_state_returns_false_not_raises():
    from tools.state_filter import matches

    # Device has neither .states nor a direct .batteryLevel attr.
    dev = _Device(onState=True)
    assert matches(dev, {"batteryLevel": "<20"}) is False


# ----- state-source preference -----------------------------------------


def test_state_from_states_dict():
    from tools.state_filter import matches

    # Only .states has the value, not a direct attr.
    dev = _Device(states={"customSensor": 5})
    assert matches(dev, {"customSensor": 5}) is True


def test_state_from_direct_attr_when_no_states_dict():
    from tools.state_filter import matches

    # No .states attribute at all — only direct attr.
    dev = _Device(brightness=50)
    assert not hasattr(dev, "states")
    assert matches(dev, {"brightness": 50}) is True


def test_empty_spec_matches_anything():
    from tools.state_filter import matches

    # No keys to check → device passes trivially.
    dev = _Device()
    assert matches(dev, {}) is True
