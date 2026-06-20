from msfs_source import PhaseEstimator


def test_parked_then_taxi_then_climb():
    pe = PhaseEstimator()
    phase, prog = pe.update(on_ground=True, alt=0, vs=0, ias=0, parking_brake=True)
    assert phase == "Parked"
    phase, _ = pe.update(on_ground=True, alt=0, vs=0, ias=12, parking_brake=False)
    assert phase == "Taxi"
    phase, _ = pe.update(on_ground=True, alt=0, vs=0, ias=90, parking_brake=False)
    assert phase == "Takeoff"
    phase, _ = pe.update(on_ground=False, alt=2000, vs=2000, ias=180, parking_brake=False)
    assert phase == "Climb"


def test_cruise_descent_approach_landing():
    pe = PhaseEstimator()
    # get airborne first so the on-ground machine knows we've departed
    pe.update(on_ground=False, alt=20000, vs=1500, ias=290, parking_brake=False)
    phase, _ = pe.update(on_ground=False, alt=35000, vs=10, ias=280, parking_brake=False)
    assert phase == "Cruise"
    phase, _ = pe.update(on_ground=False, alt=20000, vs=-1800, ias=300, parking_brake=False)
    assert phase == "Descent"
    phase, _ = pe.update(on_ground=False, alt=2500, vs=-700, ias=160, parking_brake=False)
    assert phase == "Approach"
    phase, _ = pe.update(on_ground=True, alt=0, vs=0, ias=110, parking_brake=False)
    assert phase in ("Landing", "Rollout")


def test_progress_is_unit_interval():
    pe = PhaseEstimator()
    _, prog = pe.update(on_ground=False, alt=35000, vs=0, ias=280, parking_brake=False)
    assert 0.0 <= prog <= 1.0
