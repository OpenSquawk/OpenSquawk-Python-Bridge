# tests/test_actions_gps.py
import actions

KSFO = (37.6213, -122.3790)
KLAX = (33.9416, -118.4085)  # ~543 km from KSFO


def test_no_jump_without_previous_sample():
    assert actions.is_gps_jump(None, (*KSFO, 0)) is False
    assert actions.is_gps_jump((*KSFO, 0), None) is False


def test_far_jump_with_altitude_change_is_jump():
    assert actions.is_gps_jump((*KSFO, 35000), (*KLAX, 100)) is True


def test_far_jump_but_small_altitude_change_not_below_threshold():
    # 543 km move, but both high and < 1000 ft apart -> alt condition fails
    assert actions.is_gps_jump((*KSFO, 35000), (*KLAX, 35200)) is False


def test_far_jump_with_both_low_altitude_is_jump():
    # ground-to-ground teleport: both altitudes below 1000 ft
    assert actions.is_gps_jump((*KSFO, 12), (*KLAX, 30)) is True


def test_small_move_is_not_a_jump():
    near = (KSFO[0] + 0.001, KSFO[1] + 0.001)  # ~0.1 km
    assert actions.is_gps_jump((*KSFO, 0), (*near, 0)) is False


def test_distance_just_under_50km_not_a_jump():
    # ~0.4 deg latitude north ≈ 44 km, both on the ground
    near = (KSFO[0] + 0.4, KSFO[1])
    assert actions.is_gps_jump((*KSFO, 0), (*near, 0)) is False


def test_transition_to_null_island_is_jump():
    assert actions.is_gps_jump((*KSFO, 35000), (0, 0, 0)) is True


def test_transition_from_null_island_is_jump():
    assert actions.is_gps_jump((0, 0, 0), (*KSFO, 35000)) is True


def test_both_null_island_is_not_a_jump():
    assert actions.is_gps_jump((0, 0, 0), (0, 0, 0)) is False


def test_out_of_range_latitude_counts_as_gps_discontinuity():
    assert actions.is_gps_jump((*KSFO, 35000), (999, 0, 0)) is True


def test_normalize_chain_fills_defaults_and_drops_bad_steps():
    chain = actions.normalize_chain(
        {"id": "x", "steps": [{"type": "wait", "seconds": 2}, {"type": "nope"}]}
    )
    assert chain["id"] == "x"
    assert chain["enabled"] is True
    assert chain["trigger"] is None
    assert chain["steps"] == [{"type": "wait", "seconds": 2.0}]


def test_normalize_trigger_variants():
    assert actions.normalize_trigger({"type": "hook", "hook": "sim"}) == {"type": "hook", "hook": "sim"}
    assert actions.normalize_trigger({"type": "hook", "hook": "bogus"}) is None
    assert actions.normalize_trigger({"type": "keys", "keys": ["char:a"]}) == {"type": "keys", "keys": ["char:a"]}
    assert actions.normalize_trigger({"type": "keys", "keys": []}) is None
    assert actions.normalize_trigger({"type": "joy", "joy": "X", "button": 3}) == {"type": "joy", "joy": "X", "button": 3}
    assert actions.normalize_trigger(None) is None


def test_normalize_chains_assigns_fallback_ids():
    chains = actions.normalize_chains([{"steps": []}, {"steps": []}])
    assert [c["id"] for c in chains] == ["c1", "c2"]
