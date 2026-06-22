from simulator import DummyFlight, FlightState


def test_dummy_conforms_to_source_interface():
    src = DummyFlight()
    src.open()                      # must exist (alias/reset)
    state = src.sample()
    assert isinstance(state, FlightState)
    assert state.connected is True
    assert state.aircraft           # non-empty label
    src.close()                     # must exist, no-op


def test_flightstate_has_new_fields():
    fs = FlightState(raw={}, phase="Parked", progress=0.0,
                     flight_active=False, aircraft="X", connected=True)
    assert fs.aircraft == "X"
    assert fs.connected is True


def test_dummy_read_state_is_none():
    from simulator import DummyFlight
    assert DummyFlight().read_state() is None


def test_dummy_write_state_is_noop():
    from simulator import DummyFlight
    DummyFlight().write_state({"PLANE_ALTITUDE": 1.0})  # must not raise
