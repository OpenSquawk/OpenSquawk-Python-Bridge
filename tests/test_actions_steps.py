# tests/test_actions_steps.py
import pytest
import actions


def test_wait_step_normalised_and_clamped():
    assert actions.normalize_step({"type": "wait", "seconds": 1.5}) == {"type": "wait", "seconds": 1.5}
    assert actions.normalize_step({"type": "wait", "seconds": -3})["seconds"] == 0.0
    assert actions.normalize_step({"type": "wait", "seconds": 9999})["seconds"] == 600.0


def test_key_step_requires_nonempty_keys():
    step = actions.normalize_step({"type": "key", "keys": ["key:ctrl_l", "char:m"]})
    assert step == {"type": "key", "keys": ["key:ctrl_l", "char:m"]}
    with pytest.raises(ValueError):
        actions.normalize_step({"type": "key", "keys": []})


def test_click_step_coerces_ints_and_button_default():
    step = actions.normalize_step({"type": "click", "x": 1820.7, "y": 40})
    assert step == {"type": "click", "x": 1820, "y": 40, "button": "left"}
    assert actions.normalize_step({"type": "click", "x": 0, "y": 0, "button": "right"})["button"] == "right"


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        actions.normalize_step({"type": "explode"})


def test_normalize_save_state():
    assert actions.normalize_step({"type": "save_state"}) == {"type": "save_state"}


def test_normalize_load_state():
    assert actions.normalize_step({"type": "load_state"}) == {"type": "load_state"}


def test_normalize_state_steps_ignore_extra_keys():
    assert actions.normalize_step({"type": "save_state", "junk": 1}) == {"type": "save_state"}


def test_normalize_steps_filters_and_validates_list():
    raw = [
        {"type": "wait", "seconds": 1},
        {"type": "key", "keys": ["char:a"]},
        {"type": "click", "x": 5, "y": 6},
    ]
    assert len(actions.normalize_steps(raw)) == 3
    with pytest.raises(ValueError):
        actions.normalize_steps([{"type": "nope"}])
