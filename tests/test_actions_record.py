# tests/test_actions_record.py
import actions


def test_gaps_become_wait_steps_rounded():
    events = [
        (10.00, {"type": "key", "keys": ["char:a"]}),
        (10.05, {"type": "key", "keys": ["char:b"]}),
        (11.53, {"type": "click", "x": 100, "y": 200, "button": "left"}),
    ]
    steps = actions.record_to_steps(events, min_gap=0.1)
    assert steps == [
        {"type": "key", "keys": ["char:a"]},
        {"type": "key", "keys": ["char:b"]},
        {"type": "wait", "seconds": 1.5},
        {"type": "click", "x": 100, "y": 200, "button": "left"},
    ]


def test_no_leading_wait():
    events = [(5.0, {"type": "key", "keys": ["char:x"]})]
    assert actions.record_to_steps(events) == [{"type": "key", "keys": ["char:x"]}]


def test_empty_events():
    assert actions.record_to_steps([]) == []
